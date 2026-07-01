from __future__ import annotations

import datetime as dt
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Questionnaire and question schemas
class OptionCreate(BaseModel):
    letter: str
    text: str
    is_correct: bool = False


class OptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    letter: str
    text: str
    is_correct: bool


class QuestionCreate(BaseModel):
    statement: str
    options: List[OptionCreate]


class QuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    statement: str
    options: List[OptionRead]


class QuestionnaireCreate(BaseModel):
    title: str
    description: Optional[str] = None


class QuestionnaireWithQuestionsCreate(QuestionnaireCreate):
    questions: List[QuestionCreate] = Field(default_factory=list)


class QuestionnaireUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class QuestionUpdate(BaseModel):
    statement: Optional[str] = None
    options: Optional[List[OptionCreate]] = None


class QuestionnaireRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str]
    is_active: bool = True


class QuestionnaireDetail(QuestionnaireRead):
    questions: List[QuestionRead] = []


# Attempts
class AttemptAnswerInput(BaseModel):
    question_id: int
    selected_option_id: int


class AttemptCreate(BaseModel):
    answers: List[AttemptAnswerInput] = Field(default_factory=list)


class AttemptAnswerResult(BaseModel):
    question_id: int
    selected_option_id: int
    is_correct: bool


class AttemptResult(BaseModel):
    attempt_id: int
    score: float
    total: int
    answers: List[AttemptAnswerResult]


class AttemptHistoryItem(BaseModel):
    attempt_id: int
    questionnaire_id: int
    questionnaire_title: str
    score: float
    total: int
    date: dt.datetime
    has_feedback: bool


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str
    created_at: Optional[dt.datetime] = None


# Feedback
class Citation(BaseModel):
    filename: str
    page: int
    snippet: str


class StudyItem(BaseModel):
    filename: str
    pages: List[int] = Field(default_factory=list)
    chapter: Optional[str] = None
    topic: Optional[str] = None
    reason: Optional[str] = None


class SimilarExercise(BaseModel):
    filename: str
    page: int
    description: Optional[str] = None


class StudyRecommendation(BaseModel):
    available: bool = False
    sources: List[StudyItem] = Field(default_factory=list)


class FeedbackQuestionError(BaseModel):
    question_id: int
    type: str
    message: str


class PerQuestionFeedback(BaseModel):
    question_id: int
    question_statement: Optional[str] = None
    selected_option_id: Optional[int] = None
    is_correct: bool
    status: Literal["correct", "incorrect", "partially_correct"] = "incorrect"
    explanation: str
    correct_reasoning: Optional[str] = None
    evaluation_summary: Optional[str] = None
    misconception: Optional[str] = None
    main_physical_concept: Optional[str] = None
    why_selected_answer_is_wrong: Optional[str] = None
    confidence: Literal["alta", "média", "baixa"] = "média"
    needs_teacher_review: bool = False
    related_concepts: List[str] = Field(default_factory=list)
    tip: Optional[str] = None
    study_suggestion: Optional[str] = None
    student_feedback: Optional[str] = None
    similar_question: Optional[SimilarExercise] = None
    study: List[StudyItem] = Field(default_factory=list)
    study_recommendation: Optional[StudyRecommendation] = None


class SummaryFeedback(BaseModel):
    score: float
    total: int
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)


class FeedbackMetadata(BaseModel):
    prompt_version: str = "unknown"
    validator_status: Optional[str] = None
    regeneration_count: int = 0
    quota_error_count: int = 0


class FeedbackResponse(BaseModel):
    attempt_id: int
    status: Literal["success", "partial_success"] = "success"
    summary: SummaryFeedback
    per_question: List[PerQuestionFeedback]
    global_references: List[Citation] = Field(default_factory=list)
    related_concepts: List[str] = Field(default_factory=list)
    errors: List[FeedbackQuestionError] = Field(default_factory=list)
    metadata: Optional[FeedbackMetadata] = None
