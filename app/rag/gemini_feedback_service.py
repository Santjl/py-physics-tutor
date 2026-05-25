from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import get_settings
from app.rag.errors import GeminiJSONError
from app.rag.google_client import GoogleGenAIChatClient
from app.rag.providers import RetrievedContextItem

logger = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _context_to_text(retrieved_context: list[RetrievedContextItem]) -> str:
    if not retrieved_context:
        return "Nenhum material de apoio foi recuperado."

    lines: list[str] = []
    for item in retrieved_context:
        title = item.title or "Documento sem titulo"
        page = f", pagina {item.page_number}" if item.page_number is not None else ""
        source = f", fonte {item.source_uri}" if item.source_uri else ""
        snippet = item.snippet or "Sem trecho disponivel."
        lines.append(f"- {title}{page}{source}: {snippet}")
    return "\n".join(lines)


class GeminiFeedbackService:
    def __init__(self, llm: Any | None = None) -> None:
        self.settings = get_settings()
        self.llm = llm or GoogleGenAIChatClient(temperature=0.0)

    def build_prompt(
        self,
        *,
        question: str,
        alternatives: list[dict[str, str]],
        student_answer: str,
        correct_answer: str,
        topic: str | None,
        retrieved_context: list[RetrievedContextItem],
    ) -> tuple[str, str]:
        alternatives_text = "\n".join(
            f"{item.get('label', '?')}: {item.get('text', '')}" for item in alternatives
        ) or "Sem alternativas disponiveis."
        context_text = _context_to_text(retrieved_context)
        topic_text = topic or "Nao informado"

        system_prompt = (
            "Voce e um professor particular de Fisica.\n"
            "Sua resposta final deve estar em portugues brasileiro.\n"
            "Use o contexto recuperado como fonte principal.\n"
            "Nao invente citacoes, paginas, nomes de arquivos ou fatos nao suportados pelo contexto.\n"
            "Se o contexto for insuficiente, diga isso explicitamente.\n"
            "Retorne apenas JSON valido."
        )
        user_prompt = (
            "Gere um feedback pedagogico para a questao abaixo.\n\n"
            f"Questao:\n{question}\n\n"
            f"Alternativas:\n{alternatives_text}\n\n"
            f"Resposta do aluno:\n{student_answer}\n\n"
            f"Resposta correta:\n{correct_answer}\n\n"
            f"Topico:\n{topic_text}\n\n"
            f"Contexto recuperado:\n{context_text}\n\n"
            "Estrutura JSON obrigatoria:\n"
            "{\n"
            '  "summary": "string",\n'
            '  "studentMisconception": "string",\n'
            '  "correctExplanation": "string",\n'
            '  "studyRecommendation": "string",\n'
            '  "references": [\n'
            "    {\n"
            '      "title": "string",\n'
            '      "pageNumber": 1,\n'
            '      "sourceUri": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Regras:\n"
            "- O conteudo deve estar em portugues brasileiro.\n"
            "- Seja conciso, claro e educacional.\n"
            "- Nao mencione RAG, chunks, Discovery Engine ou implementacao interna.\n"
            "- Se nao houver contexto suficiente, explique a limitacao em studyRecommendation ou correctExplanation.\n"
        )
        return system_prompt, user_prompt

    def parse_json_response(self, text: str) -> dict[str, Any]:
        cleaned = _strip_json_fence(text)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise GeminiJSONError("Gemini returned invalid JSON") from exc

        required = {"summary", "studentMisconception", "correctExplanation", "studyRecommendation", "references"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise GeminiJSONError("Gemini JSON missing required keys")
        if not isinstance(payload.get("references"), list):
            raise GeminiJSONError("Gemini JSON field 'references' must be a list")
        return payload

    def generate_question_feedback(
        self,
        *,
        question: str,
        alternatives: list[dict[str, str]],
        student_answer: str,
        correct_answer: str,
        topic: str | None,
        retrieved_context: list[RetrievedContextItem],
    ) -> dict[str, Any]:
        system_prompt, user_prompt = self.build_prompt(
            question=question,
            alternatives=alternatives,
            student_answer=student_answer,
            correct_answer=correct_answer,
            topic=topic,
            retrieved_context=retrieved_context,
        )
        logger.info("Gemini feedback model: %s", self.settings.gemini_model or self.settings.google_chat_model)
        response = self.llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        text = response.content if isinstance(response.content, str) else str(response.content)
        return self.parse_json_response(text)
