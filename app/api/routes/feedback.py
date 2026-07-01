from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.api.deps import get_db
from app.api.routes.auth import require_admin, require_student
from app.rag.feedback import generate_feedback
from app.schemas import AttemptAnswerResult, AttemptHistoryItem, AttemptResult, FeedbackResponse

router = APIRouter(prefix="/attempts", tags=["feedback"])


def _enrich_selected_options(response: FeedbackResponse, attempt: models.Attempt) -> FeedbackResponse:
    """Fill in selected_option_id and question_statement from actual answers when missing (e.g. old cached data)."""
    answer_map = {ans.question_id: ans.selected_option_id for ans in attempt.answers}
    statement_map = {ans.question_id: ans.question.statement for ans in attempt.answers}
    for pq in response.per_question:
        if pq.selected_option_id is None:
            pq.selected_option_id = answer_map.get(pq.question_id)
        if pq.question_statement is None:
            pq.question_statement = statement_map.get(pq.question_id)
    return response


def _build_history_items(attempts: list[models.Attempt]) -> list[AttemptHistoryItem]:
    return [
        AttemptHistoryItem(
            attempt_id=a.id,
            questionnaire_id=a.questionnaire_id,
            questionnaire_title=a.questionnaire.title,
            score=a.score or 0.0,
            total=a.total or 0,
            date=a.created_at,
            has_feedback=a.feedback is not None,
        )
        for a in attempts
    ]


@router.get("/me", response_model=List[AttemptHistoryItem])
def list_my_attempts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_student),
):
    attempts = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.student_id == current_user.id)
        .options(
            selectinload(models.Attempt.questionnaire),
            selectinload(models.Attempt.feedback),
        )
        .order_by(models.Attempt.created_at.desc())
    ).all()
    return _build_history_items(list(attempts))


@router.get("/", response_model=List[AttemptHistoryItem])
def list_all_attempts(
    student_id: Optional[int] = Query(None, description="Filter by student ID"),
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    stmt = (
        select(models.Attempt)
        .options(
            selectinload(models.Attempt.questionnaire),
            selectinload(models.Attempt.feedback),
        )
        .order_by(models.Attempt.created_at.desc())
    )
    if student_id is not None:
        stmt = stmt.where(models.Attempt.student_id == student_id)
    attempts = db.scalars(stmt).all()
    return _build_history_items(list(attempts))


@router.post("/{attempt_id}/feedback", response_model=FeedbackResponse, status_code=status.HTTP_200_OK)
def post_feedback(
    attempt_id: int,
    force: bool = Query(False, description="Force regeneration, ignoring cached feedback"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_student),
):
    attempt = db.get(models.Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    if attempt.student_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your attempt")

    # Return cached feedback if it already exists. Regeneration is explicit via force=True.
    existing = db.scalar(select(models.AttemptFeedback).where(models.AttemptFeedback.attempt_id == attempt_id))
    if existing and not force:
        cached = FeedbackResponse(**existing.data)
        return _enrich_selected_options(cached, attempt)

    # Ensure answers and options are loaded
    db.execute(select(models.Answer).where(models.Answer.attempt_id == attempt_id)).all()
    result = generate_feedback(db, attempt)

    if existing:
        existing.data = result.model_dump()
    else:
        db.add(models.AttemptFeedback(attempt_id=attempt_id, data=result.model_dump()))
    db.commit()

    return result


@router.get("/{attempt_id}/feedback", response_model=FeedbackResponse, status_code=status.HTTP_200_OK)
def get_feedback(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_student),
):
    attempt = db.get(models.Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    if attempt.student_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your attempt")

    record = db.scalar(select(models.AttemptFeedback).where(models.AttemptFeedback.attempt_id == attempt_id))
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found")

    return _enrich_selected_options(FeedbackResponse(**record.data), attempt)
