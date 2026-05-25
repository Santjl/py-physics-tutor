from types import SimpleNamespace

import pytest

from app.rag.gemini_feedback_service import GeminiFeedbackService
from app.rag.providers import RetrievedContextItem


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


def test_build_prompt_mentions_portuguese_and_context():
    service = GeminiFeedbackService(llm=_FakeLLM("{}"))
    system_prompt, user_prompt = service.build_prompt(
        question="O que e aceleracao?",
        alternatives=[{"label": "A", "text": "Variacao da velocidade"}],
        student_answer="Energia",
        correct_answer="A - Variacao da velocidade",
        topic="Cinematica",
        retrieved_context=[
            RetrievedContextItem(
                id="1",
                title="cap1.pdf",
                source_uri="gs://bucket/cap1.pdf",
                page_number=3,
                snippet="Aceleracao e taxa de variacao da velocidade.",
            )
        ],
    )
    assert "portugues brasileiro" in system_prompt.lower()
    assert "Contexto recuperado" in user_prompt
    assert "cap1.pdf" in user_prompt


def test_parse_json_response_accepts_fenced_json():
    service = GeminiFeedbackService(llm=_FakeLLM("{}"))
    payload = service.parse_json_response(
        """```json
{"summary":"ok","studentMisconception":"x","correctExplanation":"y","studyRecommendation":"z","references":[]}
```"""
    )
    assert payload["summary"] == "ok"


def test_parse_json_response_rejects_invalid_json():
    service = GeminiFeedbackService(llm=_FakeLLM("{}"))
    with pytest.raises(RuntimeError):
        service.parse_json_response("not-json")


def test_generate_question_feedback_returns_payload():
    content = (
        '{"summary":"Resposta incorreta.","studentMisconception":"Confundiu conceitos.",'
        '"correctExplanation":"A explicacao correta.","studyRecommendation":"Revise o topico.",'
        '"references":[{"title":"cap1.pdf","pageNumber":3,"sourceUri":"gs://bucket/cap1.pdf"}]}'
    )
    service = GeminiFeedbackService(llm=_FakeLLM(content))
    payload = service.generate_question_feedback(
        question="O que e aceleracao?",
        alternatives=[],
        student_answer="Energia",
        correct_answer="Variacao da velocidade",
        topic=None,
        retrieved_context=[],
    )
    assert payload["references"][0]["title"] == "cap1.pdf"
