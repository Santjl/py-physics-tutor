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
            )
        )
    return sanitized


def _build_study_groups(
    chunks: Sequence[models.Chunk],
    topic_text: str | None = None,
    reason: str | None = None,
) -> list[StudyItem]:
    topic = _extract_topic_from_text(topic_text)

    grouped: dict[tuple[str, str | None], set[int]] = {}
    for chunk in chunks:
        key = (chunk.filename, chunk.chapter_title)
        grouped.setdefault(key, set()).add(chunk.page)

    study_items: list[StudyItem] = []
    for (filename, chapter_title), pages in grouped.items():
        study_items.append(
            StudyItem(
                filename=filename,
                chapter=chapter_title or None,
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
    return PerQuestionFeedback(
        question_id=pq.question_id,
        is_correct=pq.is_correct,
        status=pq.status,
        explanation=_truncate_chars(pq.explanation or "", EXPLANATION_MAX_CHARS),
        correct_reasoning=_truncate_chars(pq.correct_reasoning or "", EXPLANATION_MAX_CHARS) or None,
        evaluation_summary=_truncate_chars(pq.evaluation_summary or "", EVALUATION_SUMMARY_MAX_CHARS) or None,
        misconception=_truncate_chars(pq.misconception or "", MISCONCEPTION_MAX_CHARS) if pq.misconception else None,
        related_concepts=[c.strip() for c in pq.related_concepts if c.strip()][:8],
        tip=_truncate_chars(pq.tip or "", TIP_MAX_CHARS) if pq.tip else None,
        study_suggestion=_truncate_chars(pq.study_suggestion or "", STUDY_SUGGESTION_MAX_CHARS) or None,
        student_feedback=_truncate_chars(pq.student_feedback or "", STUDENT_FEEDBACK_MAX_CHARS) or None,
        similar_question=pq.similar_question,
        study=_sanitize_study_items(pq.study),
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


def _collect_global_references(per_question: Sequence[PerQuestionFeedback]) -> list[Citation]:
    global_refs: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    for pq in per_question:
        for study in pq.study:
            for page in study.pages:
                key = (study.filename, page)
                if key not in seen:
                    seen.add(key)
                    global_refs.append(Citation(filename=study.filename, page=page, snippet=""))
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
    global_refs = _collect_global_references(per_question)
    return FeedbackResponse(
        attempt_id=attempt.id,
        summary=summary,
        per_question=per_question,
        global_references=global_refs[:8],
        related_concepts=[],
    )
