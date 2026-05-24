"""Post-generation conceptual validator for physics feedback.

After feedback is generated, a second LLM call validates that the explanation
uses physically correct reasoning and does not hallucinate concepts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

VALIDATOR_PROMPT = """\
You are a Physics 1 feedback reviewer.

Your task is to verify whether the generated feedback is physically correct and pedagogically useful.

Question:
{question_statement}

Student answer:
{selected_answer}

Correct answer:
{correct_answer}

Main physical concept identified:
{main_concept}

Generated feedback (explanation):
{explanation}

Generated feedback (why selected answer is wrong):
{why_wrong}

Retrieved sources used:
{sources_used}

Evaluate the feedback according to the following criteria:

1. Does it identify the correct physical concept?
2. Does it use the correct physical reasoning?
3. Are the formulas correctly derived from the physical situation (not attributed to wrong laws)?
4. Does it avoid unrelated concepts (e.g., using energy conservation for a relative motion problem)?
5. Does it correctly explain why the student's answer is wrong?
6. Are the cited sources actually relevant to the explanation?
7. Is the explanation clear for a Physics 1 student?
8. Is the final feedback written in Brazilian Portuguese?

Mark the feedback as INVALID if:
- It uses an incorrect physical concept to explain the problem
- It uses a correct formula but gives the wrong justification
- It cites irrelevant material
- It contradicts the question statement
- It says the answer comes from a principle that was not used
- It explains a vector problem using energy incorrectly
- It explains a relative motion problem using conservation of energy
- It explains an energy problem using force incorrectly
- It invents a textbook figure, section, page or source
- It gives a vague explanation that does not address the student's error
- It gives the correct answer but does not explain the reasoning
- It is not written in Brazilian Portuguese

Return ONLY valid JSON:
{{
  "status": "VALID" or "INVALID",
  "problem_found": "short explanation of the issue, or empty if VALID",
  "suggested_correction": "short guidance for regeneration, or empty if VALID"
}}
"""


@dataclass
class ValidationResult:
    status: str  # "VALID" or "INVALID"
    problem_found: str
    suggested_correction: str


def validate_feedback_basic(
    explanation: str,
    main_concept: str,
    status: str,
    why_wrong: Optional[str],
) -> ValidationResult:
    """Rule-based validation to avoid an extra LLM call for common quality checks."""
    text = (explanation or "").strip()
    concept = (main_concept or "").strip()

    if len(text) < 120:
        return ValidationResult(
            status="INVALID",
            problem_found="Explicacao muito curta para orientar o aluno.",
            suggested_correction="Amplie a explicacao com o raciocinio fisico passo a passo.",
        )

    if not concept:
        return ValidationResult(
            status="INVALID",
            problem_found="Conceito fisico principal nao foi identificado.",
            suggested_correction="Declare explicitamente o conceito fisico principal antes da explicacao.",
        )

    if status != "correct" and not (why_wrong or "").strip():
        return ValidationResult(
            status="INVALID",
            problem_found="Nao explicou por que a alternativa marcada esta errada.",
            suggested_correction="Inclua uma justificativa objetiva do erro da alternativa escolhida.",
        )

    return ValidationResult(status="VALID", problem_found="", suggested_correction="")


def validate_feedback(
    llm,
    question_statement: str,
    selected_answer: str,
    correct_answer: str,
    main_concept: str,
    explanation: str,
    why_wrong: Optional[str],
    sources_used: str,
) -> ValidationResult:
    """Validate generated feedback for conceptual correctness.

    Returns ValidationResult. On LLM failure, returns VALID (graceful degradation).
    """
    settings = get_settings()

    if not settings.feedback_enable_validator:
        return ValidationResult(status="VALID", problem_found="", suggested_correction="")

    prompt = VALIDATOR_PROMPT.format(
        question_statement=question_statement,
        selected_answer=selected_answer,
        correct_answer=correct_answer,
        main_concept=main_concept or "Nao identificado",
        explanation=explanation or "",
        why_wrong=why_wrong or "Nao fornecido",
        sources_used=sources_used or "Nenhuma fonte citada",
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content="You are a physics feedback validator. Return only JSON."),
            HumanMessage(content=prompt),
        ]
        result = llm.invoke(messages)
        text = result.content if isinstance(result.content, str) else str(result.content)

        # Parse JSON from response
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object")

        return ValidationResult(
            status=data.get("status", "VALID").upper(),
            problem_found=data.get("problem_found", ""),
            suggested_correction=data.get("suggested_correction", ""),
        )

    except Exception:
        logger.warning("Feedback validation failed, assuming VALID", exc_info=True)
        return ValidationResult(status="VALID", problem_found="", suggested_correction="")
