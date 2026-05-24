from __future__ import annotations

import logging
import time
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Mapping, Sequence

from langchain_community.chat_models import ChatOllama

from app import models
from app.core.config import get_settings
from app.rag.feedback_builder import (
    _build_study_groups,
    _build_summary,
    _collect_global_concepts,
    _collect_global_references,
    _default_feedback_from_per_q,
    _default_per_question_feedback,
    _sanitize_per_question_feedback,
    _strip_where_to_study,
    _truncate_text,
)
from app.rag.feedback_constants import (
    CHUNK_TEXT_MAX_CHARS,
    EXPLANATION_MAX_CHARS,
    FEEDBACK_PROMPT_VERSION,
    MAX_FEEDBACK_REGENERATION_ATTEMPTS,
    MISCONCEPTION_MAX_CHARS,
    TIP_MAX_CHARS,
)
from app.rag.feedback_parser import (
    _extract_evaluation_summary,
    _extract_explanation,
    _extract_main_physical_concept,
    _extract_misconception,
    _extract_related_concepts,
    _extract_similar_exercise,
    _extract_status,
    _extract_student_feedback,
    _extract_study_suggestion,
    _extract_study_text,
    _extract_tip,
    _extract_why_wrong,
    _parse_llm_sections,
    extract_source_ids,
    invoke_llm_for_question,
    map_source_ids_to_chunks,
)
from app.rag.feedback_prompts import build_system_prompt_per_question, build_user_prompt_for_question
from app.rag.google_client import GoogleGenAIChatClient
from app.rag.feedback_validator import ValidationResult, validate_feedback, validate_feedback_basic
from app.rag.relevance_filter import filter_chunks_by_relevance
from app.rag.retrieval import embed_queries, retrieve_chunks, retrieve_exercise_chunks
from app.schemas import FeedbackMetadata, FeedbackQuestionError, FeedbackResponse, PerQuestionFeedback

logger = logging.getLogger(__name__)


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).upper()
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "TOO MANY REQUESTS" in text


class _AttemptCircuitState:
    def __init__(self, max_quota_errors: int) -> None:
        self.max_quota_errors = max(1, max_quota_errors)
        self.quota_errors = 0
        self._lock = threading.Lock()

    def register_exception(self, exc: Exception) -> None:
        if not _is_quota_error(exc):
            return
        with self._lock:
            self.quota_errors += 1

    def quota_exceeded(self) -> bool:
        with self._lock:
            return self.quota_errors >= self.max_quota_errors


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _retrieval_query_for_answer(ans: models.Answer) -> str:
    correct_opt = next((o for o in ans.question.options if o.is_correct), None)
    correct_txt = f"{correct_opt.letter} - {correct_opt.text}" if correct_opt else "Unknown"
    selected_txt = f"{ans.option.letter} - {ans.option.text}"
    return (
        f"{ans.question.statement}\n"
        f"Selected: {selected_txt}\n"
        f"Correct option: {correct_txt}"
    )


def _retrieve_per_question(
    db,
    attempt: models.Attempt,
    top_k: int = 4,
    exercise_top_k: int = 2,
) -> tuple[Mapping[int, Sequence[models.Chunk]], Mapping[int, Sequence[models.Chunk]]]:
    """Retrieve theory and exercise chunks per incorrect question.

    Returns (per_q_theory, per_q_exercises).
    """
    per_q: dict[int, list[models.Chunk]] = {}
    per_q_ex: dict[int, list[models.Chunk]] = {}
    incorrect_answers = [ans for ans in attempt.answers if not ans.is_correct]
    queries = [_retrieval_query_for_answer(ans) for ans in incorrect_answers]

    vectors_by_qid: dict[int, list[float] | None] = {ans.question_id: None for ans in incorrect_answers}
    if queries:
        try:
            query_vecs = embed_queries(queries)
            for ans, vec in zip(incorrect_answers, query_vecs):
                vectors_by_qid[ans.question_id] = vec
        except Exception:
            logger.warning("Failed to batch embed retrieval queries, falling back", exc_info=True)

    for ans, q in zip(incorrect_answers, queries):
        q_vec = vectors_by_qid.get(ans.question_id)
        per_q[ans.question_id] = list(retrieve_chunks(db, query=q, top_k=top_k, query_vec=q_vec))
        per_q_ex[ans.question_id] = list(
            retrieve_exercise_chunks(db, query=q, top_k=exercise_top_k, query_vec=q_vec),
        )
    return per_q, per_q_ex


# ---------------------------------------------------------------------------
# LLM invocation (parallel)
# ---------------------------------------------------------------------------

def _process_one_question(
    llm,
    system_prompt: str,
    ans: models.Answer,
    chunks: Sequence[models.Chunk],
    exercise_chunks: Sequence[models.Chunk],
    validation_semaphore: threading.BoundedSemaphore | None = None,
    circuit_state: _AttemptCircuitState | None = None,
) -> PerQuestionFeedback:
    """Build PerQuestionFeedback for a single answer by calling the LLM.

    Includes relevance filtering, conceptual validation, and regeneration loop.
    """
    user_prompt, source_map, exercise_map = build_user_prompt_for_question(ans, chunks, exercise_chunks)
    fallback = False
    t1 = time.perf_counter()

    try:
        # --- Relevance filtering ---
        settings = get_settings()
        use_relevance_filter = settings.feedback_enable_relevance_filter
        if circuit_state is not None and circuit_state.quota_exceeded():
            use_relevance_filter = False

        scored_chunks = []
        if use_relevance_filter:
            scored_chunks = filter_chunks_by_relevance(
                llm, ans.question.statement, chunks, source_map,
            )
        if scored_chunks:
            filtered_source_map = {sc.source_id: sc.chunk for sc in scored_chunks}
            filtered_chunks = [sc.chunk for sc in scored_chunks]
        else:
            # No relevant chunks — proceed without sources
            filtered_source_map = {}
            filtered_chunks = []

        # Rebuild user prompt with filtered chunks if filtering changed the set
        if len(filtered_source_map) != len(source_map):
            user_prompt, source_map, exercise_map = build_user_prompt_for_question(
                ans, filtered_chunks, exercise_chunks,
            )

        # --- Generation + validation loop ---
        max_attempts = settings.feedback_max_regeneration_attempts
        correction_hint: str | None = None
        validation_result: ValidationResult | None = None

        for attempt_num in range(max_attempts + 1):
            prompt_to_send = user_prompt
            if correction_hint:
                prompt_to_send += (
                    f"\n\nCORRECAO SOLICITADA (tentativa {attempt_num}):\n"
                    f"{correction_hint}\n"
                    "Gere o feedback novamente corrigindo o problema indicado."
                )

            text = invoke_llm_for_question(llm, system_prompt, prompt_to_send)
            logger.info("LLM raw attempt %d (truncated): %r", attempt_num, _truncate_text(text, limit=500))

            sections = _parse_llm_sections(text)
            ids = extract_source_ids(text, set(source_map.keys()))
            cited_chunks = map_source_ids_to_chunks(ids, source_map)
            study_chunks = cited_chunks if cited_chunks else list(chunks)

            explanation = _extract_explanation(sections) or _strip_where_to_study(text)
            main_concept = _extract_main_physical_concept(sections)
            why_wrong = _extract_why_wrong(sections)
            study_text = _extract_study_text(sections)
            study = _build_study_groups(study_chunks, topic_text=study_text, reason=study_text)

            status = _extract_status(sections, ans.is_correct)
            evaluation_summary = _extract_evaluation_summary(sections)
            related_concepts = _extract_related_concepts(sections)
            study_suggestion = _extract_study_suggestion(sections)
            student_feedback = _extract_student_feedback(sections)

            basic_validation = validate_feedback_basic(
                explanation=explanation,
                main_concept=main_concept or "",
                status=status,
                why_wrong=why_wrong,
            )
            if basic_validation.status == "INVALID" and attempt_num < max_attempts:
                correction_hint = (
                    f"Problema encontrado: {basic_validation.problem_found}\n"
                    f"Correcao sugerida: {basic_validation.suggested_correction}"
                )
                continue

            # --- Conceptual validation (optional LLM pass) ---
            use_llm_validator = settings.feedback_enable_validator
            if circuit_state is not None and circuit_state.quota_exceeded():
                use_llm_validator = False

            if use_llm_validator and attempt_num < max_attempts:
                correct_opt = next((o for o in ans.question.options if o.is_correct), None)
                correct_txt = f"{correct_opt.letter} - {correct_opt.text}" if correct_opt else ""
                selected_txt = f"{ans.option.letter} - {ans.option.text}"
                sources_desc = ", ".join(
                    f"{sid}: {c.filename} p.{c.page}" for sid, c in source_map.items()
                ) if source_map else "Nenhuma"

                if validation_semaphore is not None:
                    with validation_semaphore:
                        validation_result = validate_feedback(
                            llm,
                            question_statement=ans.question.statement,
                            selected_answer=selected_txt,
                            correct_answer=correct_txt,
                            main_concept=main_concept or "",
                            explanation=explanation,
                            why_wrong=why_wrong,
                            sources_used=sources_desc,
                        )
                else:
                    validation_result = validate_feedback(
                        llm,
                        question_statement=ans.question.statement,
                        selected_answer=selected_txt,
                        correct_answer=correct_txt,
                        main_concept=main_concept or "",
                        explanation=explanation,
                        why_wrong=why_wrong,
                        sources_used=sources_desc,
                    )

                if validation_result.status == "INVALID":
                    logger.warning(
                        "Feedback INVALID for question %d (attempt %d): %s",
                        ans.question_id,
                        attempt_num,
                        validation_result.problem_found,
                    )
                    correction_hint = (
                        f"Problema encontrado: {validation_result.problem_found}\n"
                        f"Correcao sugerida: {validation_result.suggested_correction}"
                    )
                    continue  # Retry
            # Valid or last attempt — break out
            break

        # --- Build result ---
        needs_review = (
            validation_result is not None and validation_result.status == "INVALID"
        )
        confidence: str = "alta"
        if needs_review:
            confidence = "baixa"
        elif not main_concept:
            confidence = "média"

        # If still invalid after all attempts, use safe fallback
        if needs_review:
            pq = _build_safe_fallback(ans, chunks, main_concept)
        else:
            pq = PerQuestionFeedback(
                question_id=ans.question_id,
                selected_option_id=ans.selected_option_id,
                is_correct=False,
                status=status,
                explanation=explanation,
                correct_reasoning=explanation,
                evaluation_summary=evaluation_summary,
                misconception=_extract_misconception(sections),
                main_physical_concept=main_concept,
                why_selected_answer_is_wrong=why_wrong,
                confidence=confidence,
                needs_teacher_review=False,
                related_concepts=related_concepts,
                tip=_extract_tip(sections),
                study_suggestion=study_suggestion,
                student_feedback=student_feedback,
                similar_question=_extract_similar_exercise(
                    sections, exercise_map, exercise_chunks,
                    theory_chunks=chunks, source_map=source_map,
                ),
                study=study,
            )
        return _sanitize_per_question_feedback(pq)

    except Exception as exc:  # noqa: BLE001
        fallback = True
        if circuit_state is not None:
            circuit_state.register_exception(exc)
        logger.exception("LLM failed for question %d, falling back", ans.question_id)
        return _sanitize_per_question_feedback(_default_per_question_feedback(ans, chunks))
    finally:
        logger.info(
            "llm.invoke.question: %.2fs (question_id=%d fallback=%s)",
            time.perf_counter() - t1,
            ans.question_id,
            fallback,
        )


def _build_safe_fallback(
    ans: models.Answer,
    chunks: Sequence[models.Chunk],
    main_concept: str | None = None,
) -> PerQuestionFeedback:
    """Build conservative fallback feedback when validation fails."""
    if main_concept:
        explanation = (
            f"A resposta selecionada esta incorreta. A resolucao desta questao depende do "
            f"conceito de {main_concept}. A alternativa marcada provavelmente esta relacionada "
            f"a uma interpretacao inadequada das grandezas envolvidas no problema."
        )
    else:
        explanation = (
            "A resposta selecionada esta incorreta. A resolucao desta questao depende de um "
            "conceito fisico que precisa ser analisado com cuidado. A alternativa marcada "
            "provavelmente esta relacionada a uma interpretacao inadequada das grandezas "
            "envolvidas no problema."
        )

    return PerQuestionFeedback(
        question_id=ans.question_id,
        selected_option_id=ans.selected_option_id,
        is_correct=False,
        status="incorrect",
        explanation=explanation,
        correct_reasoning=explanation,
        evaluation_summary=None,
        misconception=None,
        main_physical_concept=main_concept,
        why_selected_answer_is_wrong=None,
        confidence="baixa",
        needs_teacher_review=True,
        related_concepts=[main_concept] if main_concept else [],
        tip="Releia o enunciado com atencao e identifique as grandezas envolvidas.",
        study_suggestion=(
            "Este item deve ser revisado pelo professor para garantir uma explicacao mais precisa."
        ),
        student_feedback=(
            "O sistema nao conseguiu gerar uma explicacao confiavel para esta questao. "
            "Peca ajuda ao professor para revisar este item."
        ),
        similar_question=None,
        study=_build_study_groups(chunks[:2]) if chunks else [],
    )


def _generate_feedback_with_llm(
    llm,
    attempt: models.Attempt,
    per_q_chunks: Mapping[int, Sequence[models.Chunk]],
    per_q_exercises: Mapping[int, Sequence[models.Chunk]] | None = None,
) -> FeedbackResponse:
    settings = get_settings()
    system_prompt = build_system_prompt_per_question()
    per_q_exercises = per_q_exercises or {}
    incorrect_answers = [ans for ans in attempt.answers if not ans.is_correct]
    if not incorrect_answers:
        return FeedbackResponse(
            attempt_id=attempt.id,
            summary=_build_summary(attempt),
            per_question=[],
            global_references=[],
            related_concepts=[],
            metadata=FeedbackMetadata(
                prompt_version=FEEDBACK_PROMPT_VERSION,
                validator_status="VALID",
                regeneration_count=0,
            ),
        )

    max_workers = max(1, min(len(incorrect_answers), settings.max_concurrent_llm_calls))
    question_timeout = max(1, settings.question_feedback_timeout_seconds)
    validation_semaphore = threading.BoundedSemaphore(max(1, settings.max_concurrent_validations))
    circuit_state = _AttemptCircuitState(settings.max_quota_errors_per_attempt)
    answer_by_qid = {ans.question_id: ans for ans in incorrect_answers}

    results: dict[int, PerQuestionFeedback] = {}
    question_errors: list[FeedbackQuestionError] = []
    start_times: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_qid = {
            pool.submit(
                _process_one_question,
                llm,
                system_prompt,
                ans,
                per_q_chunks.get(ans.question_id, []),
                per_q_exercises.get(ans.question_id, []),
                validation_semaphore,
                circuit_state,
            ): ans.question_id
            for ans in incorrect_answers
        }

        for future in future_to_qid:
            start_times[future] = time.perf_counter()

        pending = set(future_to_qid)
        while pending:
            done, not_done = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)

            for future in done:
                qid = future_to_qid[future]
                try:
                    results[qid] = future.result()
                except Exception:
                    logger.exception("Question %d failed, using fallback", qid)
                    ans = answer_by_qid[qid]
                    fallback = _default_per_question_feedback(ans, per_q_chunks.get(qid, []))
                    results[qid] = _sanitize_per_question_feedback(fallback)
                    question_errors.append(
                        FeedbackQuestionError(
                            question_id=qid,
                            type="generation_failed",
                            message="Falha ao gerar feedback com LLM. Fallback aplicado.",
                        )
                    )

            now = time.perf_counter()
            timed_out = {
                future for future in not_done
                if now - start_times.get(future, now) > question_timeout
            }
            for future in timed_out:
                qid = future_to_qid[future]
                logger.warning("Question %d timed out after %ds, using fallback", qid, question_timeout)
                ans = answer_by_qid[qid]
                fallback = _default_per_question_feedback(ans, per_q_chunks.get(qid, []))
                fallback.student_feedback = (
                    "Nao foi possivel gerar um feedback completo para esta questao neste momento. "
                    "Tente novamente mais tarde."
                )
                results[qid] = _sanitize_per_question_feedback(fallback)
                question_errors.append(
                    FeedbackQuestionError(
                        question_id=qid,
                        type="timeout",
                        message="A geracao de feedback excedeu o tempo limite configurado.",
                    )
                )

            pending = set(not_done) - timed_out

    per_question = [results[ans.question_id] for ans in incorrect_answers if ans.question_id in results]
    summary = _build_summary(attempt)
    global_refs = _collect_global_references(per_question)[:8]
    global_concepts = _collect_global_concepts(per_question)

    # Determine overall validator status
    has_review = any(pq.needs_teacher_review for pq in per_question)
    validator_status = "FALLBACK" if has_review else "VALID"
    response_status = "partial_success" if question_errors else "success"

    return FeedbackResponse(
        attempt_id=attempt.id,
        status=response_status,
        summary=summary,
        per_question=per_question,
        global_references=global_refs,
        related_concepts=global_concepts,
        errors=question_errors,
        metadata=FeedbackMetadata(
            prompt_version=FEEDBACK_PROMPT_VERSION,
            validator_status=validator_status,
            regeneration_count=0,
            quota_error_count=circuit_state.quota_errors,
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_feedback(db, attempt: models.Attempt) -> FeedbackResponse:
    settings = get_settings()

    t0 = time.perf_counter()
    per_q_chunks, per_q_exercises = _retrieve_per_question(db, attempt, top_k=4, exercise_top_k=2)
    logger.info(
        "retrieve_per_question: %.2fs (questions=%d)",
        time.perf_counter() - t0,
        sum(1 for ans in attempt.answers if not ans.is_correct),
    )

    if settings.app_env.lower() == "test":
        return _default_feedback_from_per_q(attempt, per_q_chunks)

    provider = settings.llm_provider.lower().strip()
    if provider == "ollama":
        llm = ChatOllama(
            base_url=settings.ollama_base_url,
            model=settings.ollama_chat_model,
            temperature=0.0,
        )
    else:
        llm = GoogleGenAIChatClient(temperature=0.0)

    try:
        return _generate_feedback_with_llm(llm, attempt, per_q_chunks, per_q_exercises)
    except Exception:  # noqa: BLE001
        logger.exception("Feedback generation failed, falling back to default")
        return _default_feedback_from_per_q(attempt, per_q_chunks)

