from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    MISCONCEPTION_MAX_CHARS,
    TIP_MAX_CHARS,
)
from app.rag.feedback_parser import (
    _extract_evaluation_summary,
    _extract_explanation,
    _extract_misconception,
    _extract_related_concepts,
    _extract_similar_exercise,
    _extract_status,
    _extract_student_feedback,
    _extract_study_suggestion,
    _extract_study_text,
    _extract_tip,
    _parse_llm_sections,
    extract_source_ids,
    invoke_llm_for_question,
    map_source_ids_to_chunks,
)
from app.rag.feedback_prompts import build_system_prompt_per_question, build_user_prompt_for_question
from app.rag.retrieval import embed_query, retrieve_chunks, retrieve_exercise_chunks
from app.schemas import FeedbackResponse, PerQuestionFeedback

logger = logging.getLogger(__name__)


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
    for ans in attempt.answers:
        if ans.is_correct:
            continue
        q = _retrieval_query_for_answer(ans)
        try:
            q_vec: list[float] | None = embed_query(q)
        except Exception:
            logger.warning("Failed to embed query for question %d, falling back", ans.question_id)
            q_vec = None
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
) -> PerQuestionFeedback:
    """Build PerQuestionFeedback for a single answer by calling the LLM."""
    user_prompt, source_map, exercise_map = build_user_prompt_for_question(ans, chunks, exercise_chunks)
    fallback = False
    t1 = time.perf_counter()

    try:
        text = invoke_llm_for_question(llm, system_prompt, user_prompt)
        logger.info("LLM raw (truncated): %r", _truncate_text(text, limit=500))

        sections = _parse_llm_sections(text)
        ids = extract_source_ids(text, set(source_map.keys()))
        cited_chunks = map_source_ids_to_chunks(ids, source_map)
        study_chunks = cited_chunks if cited_chunks else chunks

        explanation = _extract_explanation(sections) or _strip_where_to_study(text)
        study_text = _extract_study_text(sections)
        study = _build_study_groups(study_chunks, topic_text=study_text, reason=study_text)

        status = _extract_status(sections, ans.is_correct)
        evaluation_summary = _extract_evaluation_summary(sections)
        related_concepts = _extract_related_concepts(sections)
        study_suggestion = _extract_study_suggestion(sections)
        student_feedback = _extract_student_feedback(sections)

        pq = PerQuestionFeedback(
            question_id=ans.question_id,
            is_correct=False,
            status=status,
            explanation=explanation,
            correct_reasoning=explanation,
            evaluation_summary=evaluation_summary,
            misconception=_extract_misconception(sections),
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
    except Exception:  # noqa: BLE001
        fallback = True
        logger.exception("LLM failed for question %d, falling back", ans.question_id)
        return _sanitize_per_question_feedback(_default_per_question_feedback(ans, chunks))
    finally:
        logger.info(
            "llm.invoke.question: %.2fs (question_id=%d fallback=%s)",
            time.perf_counter() - t1,
            ans.question_id,
            fallback,
        )


def _generate_feedback_with_llm(
    llm,
    attempt: models.Attempt,
    per_q_chunks: Mapping[int, Sequence[models.Chunk]],
    per_q_exercises: Mapping[int, Sequence[models.Chunk]] | None = None,
) -> FeedbackResponse:
    system_prompt = build_system_prompt_per_question()
    per_q_exercises = per_q_exercises or {}
    incorrect_answers = [ans for ans in attempt.answers if not ans.is_correct]
    max_workers = min(len(incorrect_answers), 4)

    results: dict[int, PerQuestionFeedback] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_qid = {
            pool.submit(
                _process_one_question,
                llm,
                system_prompt,
                ans,
                per_q_chunks.get(ans.question_id, []),
                per_q_exercises.get(ans.question_id, []),
            ): ans.question_id
            for ans in incorrect_answers
        }
        for future in as_completed(future_to_qid):
            results[future_to_qid[future]] = future.result()

    per_question = [results[ans.question_id] for ans in incorrect_answers if ans.question_id in results]
    summary = _build_summary(attempt)
    global_refs = _collect_global_references(per_question)[:8]
    global_concepts = _collect_global_concepts(per_question)
    return FeedbackResponse(
        attempt_id=attempt.id,
        summary=summary,
        per_question=per_question,
        global_references=global_refs,
        related_concepts=global_concepts,
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

    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_chat_model,
        temperature=0.0,
    )

    try:
        return _generate_feedback_with_llm(llm, attempt, per_q_chunks, per_q_exercises)
    except Exception:  # noqa: BLE001
        logger.exception("Feedback generation failed, falling back to default")
        return _default_feedback_from_per_q(attempt, per_q_chunks)

