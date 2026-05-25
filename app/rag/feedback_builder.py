"""Domain object builders and sanitizers for the feedback pipeline."""
from __future__ import annotations

import re
from typing import List, Mapping, Sequence

from app import models
from app.rag.feedback_constants import (
    EVALUATION_SUMMARY_MAX_CHARS,
    EXPLANATION_MAX_CHARS,
    MISCONCEPTION_MAX_CHARS,
    STUDENT_FEEDBACK_MAX_CHARS,
    STUDY_SUGGESTION_MAX_CHARS,
    TIP_MAX_CHARS,
    WHY_WRONG_MAX_CHARS,
)
from app.schemas import (
    Citation,
    FeedbackResponse,
    PerQuestionFeedback,
    SimilarExercise,
    StudyItem,
    SummaryFeedback,
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _truncate_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _truncate_text(text: str, limit: int = 300) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _strip_where_to_study(text: str) -> str:
    lower = text.lower()
    marker = "onde estudar no livro:"
    idx = lower.find(marker)
    if idx == -1:
        return text.strip()
    return text[:idx].strip()


def _format_pages(pages: Sequence[int]) -> str:
    unique_pages = sorted(set(pages))
    if not unique_pages:
        return ""
    if len(unique_pages) == 1:
        return f"pagina {unique_pages[0]}"
    return "paginas " + ", ".join(str(page) for page in unique_pages)


def _build_study_location_text(study: Sequence[StudyItem]) -> str | None:
    parts: list[str] = []
    for item in study:
        detail_parts = [item.filename]
        pages_text = _format_pages(item.pages)
        if pages_text:
            detail_parts.append(pages_text)
        if item.chapter:
            detail_parts.append(item.chapter)
        if item.topic:
            detail_parts.append(f"topico {item.topic}")
        parts.append(", ".join(detail_parts))

    if not parts:
        return None
    return "Onde estudar: " + "; ".join(parts) + "."


def _append_study_location_text(text: str | None, study: Sequence[StudyItem]) -> str | None:
    base = (text or "").strip()
    location_text = _build_study_location_text(study)
    if not location_text:
        return base or None
    if location_text in base:
        return base or None
    if not base:
        return location_text
    return f"{base}\n\n{location_text}"


# ---------------------------------------------------------------------------
# Study item builders
# ---------------------------------------------------------------------------

def _extract_topic_from_text(text: str) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"^\s*-\s*", "", text.strip())
    cleaned = re.sub(r"\(S\d+\)", "", cleaned).strip()
    first_line = cleaned.split("\n")[0].strip()
    return first_line if first_line else None


def _sanitize_study_items(study: Sequence[StudyItem]) -> list[StudyItem]:
    sanitized: list[StudyItem] = []
    for item in study:
        sanitized.append(
            StudyItem(
                filename=item.filename,
                pages=sorted(set(item.pages)),
                chapter=item.chapter if item.chapter else None,
                topic=item.topic if item.topic else None,
                reason=item.reason if item.reason else None,
            )
        )
    return sanitized


def _build_study_groups(
    chunks: Sequence[models.Chunk],
    topic_text: str | None = None,
    reason: str | None = None,
) -> list[StudyItem]:
    topic = _extract_topic_from_text(topic_text)

    grouped: dict[tuple[str, str | None, str | None], set[int]] = {}
    for chunk in chunks:
        key = (chunk.filename, chunk.chapter_title, chunk.section_title)
        grouped.setdefault(key, set()).add(chunk.page)

    study_items: list[StudyItem] = []
    for (filename, chapter_title, section_title), pages in grouped.items():
        if section_title and chapter_title:
            chapter_label = f"{chapter_title} / {section_title}"
        else:
            chapter_label = chapter_title or section_title or None
        study_items.append(
            StudyItem(
                filename=filename,
                chapter=chapter_label,
                pages=sorted(pages),
                topic=topic,
                reason=reason,
            )
        )
    return _sanitize_study_items(study_items)


# ---------------------------------------------------------------------------
# PerQuestionFeedback builders
# ---------------------------------------------------------------------------

def _sanitize_per_question_feedback(pq: PerQuestionFeedback) -> PerQuestionFeedback:
    sanitized_study = _sanitize_study_items(pq.study)
    return PerQuestionFeedback(
        question_id=pq.question_id,
        selected_option_id=pq.selected_option_id,
        is_correct=pq.is_correct,
        status=pq.status,
        explanation=_truncate_chars(pq.explanation or "", EXPLANATION_MAX_CHARS),
        correct_reasoning=_truncate_chars(pq.correct_reasoning or "", EXPLANATION_MAX_CHARS) or None,
        evaluation_summary=_truncate_chars(pq.evaluation_summary or "", EVALUATION_SUMMARY_MAX_CHARS) or None,
        misconception=_truncate_chars(pq.misconception or "", MISCONCEPTION_MAX_CHARS) if pq.misconception else None,
        main_physical_concept=pq.main_physical_concept,
        why_selected_answer_is_wrong=_truncate_chars(pq.why_selected_answer_is_wrong or "", WHY_WRONG_MAX_CHARS) or None,
        confidence=pq.confidence,
        needs_teacher_review=pq.needs_teacher_review,
        related_concepts=[c.strip() for c in pq.related_concepts if c.strip()][:8],
        tip=_truncate_chars(pq.tip or "", TIP_MAX_CHARS) if pq.tip else None,
        study_suggestion=(
            _truncate_chars(
                _append_study_location_text(pq.study_suggestion, sanitized_study) or "",
                STUDY_SUGGESTION_MAX_CHARS,
            ) or None
        ),
        student_feedback=(
            _truncate_chars(
                _append_study_location_text(pq.student_feedback, sanitized_study) or "",
                STUDENT_FEEDBACK_MAX_CHARS,
            ) or None
        ),
        similar_question=pq.similar_question,
        study=sanitized_study,
        study_recommendation=pq.study_recommendation,
    )


def _default_per_question_feedback(
    ans: models.Answer,
    chunks: Sequence[models.Chunk],
) -> PerQuestionFeedback:
    explanation = "Revise o conceito e compare com as fontes indicadas." if chunks else "Revise o conceito."
    study_suggestion = (
        "Revise o conteudo indicado nas fontes abaixo antes de tentar questoes semelhantes."
        if chunks else "Procure o conteudo relacionado no material de estudo."
    )
    similar_question: SimilarExercise | None = None
    if chunks:
        similar_question = SimilarExercise(
            filename=chunks[0].filename,
            page=chunks[0].page,
            description="Procure exercicios sobre este tema proximo a esta pagina no material.",
        )
    return PerQuestionFeedback(
        question_id=ans.question_id,
        selected_option_id=ans.selected_option_id,
        is_correct=ans.is_correct,
        status="correct" if ans.is_correct else "incorrect",
        explanation=explanation,
        correct_reasoning=explanation,
        evaluation_summary=None,
        misconception="Nao foi possivel determinar o raciocinio do aluno automaticamente.",
        related_concepts=[],
        tip="Releia o enunciado com atencao e confira as unidades.",
        study_suggestion=study_suggestion,
        student_feedback=None,
        similar_question=similar_question,
        study=_build_study_groups(chunks[:4]) if chunks else [],
    )


# ---------------------------------------------------------------------------
# FeedbackResponse builders
# ---------------------------------------------------------------------------

def _build_summary(attempt: models.Attempt) -> SummaryFeedback:
    score = attempt.score or 0.0
    total = attempt.total or 0
    return SummaryFeedback(
        score=score,
        total=total,
        strengths=["Respondeu corretamente"] if score > 0 else [],
        weaknesses=["Questoes com erro"] if score < total else [],
    )


def _citation_snippet_from_chunk(chunk: models.Chunk, limit: int = 220) -> str:
    snippet = (chunk.text or "").replace("\n", " ").strip()
    return _truncate_text(snippet, limit=limit)


def _collect_global_references(
    per_question: Sequence[PerQuestionFeedback],
    per_q_chunks: Mapping[int, Sequence[models.Chunk]] | None = None,
) -> list[Citation]:
    global_refs: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    per_q_chunks = per_q_chunks or {}
    for pq in per_question:
        chunk_lookup = {
            (chunk.filename, chunk.page): chunk
            for chunk in per_q_chunks.get(pq.question_id, [])
        }
        for study in pq.study:
            for page in study.pages:
                key = (study.filename, page)
                if key not in seen:
                    seen.add(key)
                    chunk = chunk_lookup.get(key)
                    global_refs.append(
                        Citation(
                            filename=study.filename,
                            page=page,
                            snippet=_citation_snippet_from_chunk(chunk) if chunk else "",
                        )
                    )
    return global_refs


def _collect_global_concepts(per_question: Sequence[PerQuestionFeedback]) -> list[str]:
    """Deduplicated merge of related_concepts across all per-question feedback, preserving order."""
    seen: set[str] = set()
    merged: list[str] = []
    for pq in per_question:
        for concept in pq.related_concepts:
            normalised = concept.strip()
            if normalised and normalised not in seen:
                seen.add(normalised)
                merged.append(normalised)
    return merged[:15]


def _default_feedback_from_per_q(
    attempt: models.Attempt,
    per_q_chunks: Mapping[int, Sequence[models.Chunk]],
) -> FeedbackResponse:
    per_question: List[PerQuestionFeedback] = []
    for ans in attempt.answers:
        if ans.is_correct:
            continue
        chunks = per_q_chunks.get(ans.question_id, [])
        per_question.append(_sanitize_per_question_feedback(_default_per_question_feedback(ans, chunks)))

    summary = _build_summary(attempt)
    global_refs = _collect_global_references(per_question, per_q_chunks)
    return FeedbackResponse(
        attempt_id=attempt.id,
        summary=summary,
        per_question=per_question,
        global_references=global_refs[:8],
        related_concepts=[],
    )
