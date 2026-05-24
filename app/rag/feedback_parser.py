"""LLM output parsing and source-ID extraction for the feedback pipeline."""
from __future__ import annotations

import re
from typing import Literal, Mapping, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from app import models

# Section headers the LLM may use (normalised to lowercase for matching).
_SECTION_HEADERS = [
    "conceito fisico principal",
    "avaliacao",
    "explicacao",
    "raciocinio correto",
    "raciocinio simulado",
    "por que a resposta esta errada",
    "erro conceitual do aluno",
    "erro conceitual",
    "possivel confusao",
    "conceitos relacionados",
    "onde estudar no livro",
    "questao similar",
    "exercicio similar",
    "sugestao de estudo",
    "dica",
    "dicas",
    "mensagem para o aluno",
]


def _parse_llm_sections(text: str) -> dict[str, str]:
    """Split LLM output into named sections.

    Returns a dict keyed by the *lowercased* header name with content as value.
    """
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        colon_idx = stripped.find(":")
        if colon_idx > 0:
            candidate = stripped[:colon_idx].strip().lower()
            rest_after_colon = stripped[colon_idx + 1:].strip()
            if candidate in _SECTION_HEADERS:
                if current_key is not None:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = candidate
                current_lines = [rest_after_colon] if rest_after_colon else []
                continue
        if current_key is not None:
            current_lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def invoke_llm_for_question(llm, system_prompt: str, user_prompt: str) -> str:
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    result = llm.invoke(messages)
    return result.content if isinstance(result.content, str) else str(result.content)


def extract_source_ids(text: str, valid_ids: set[str]) -> list[str]:
    if not text or not valid_ids:
        return []
    found = re.findall(r"\bS\d+\b", text)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item in valid_ids and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def map_source_ids_to_chunks(
    ids: Sequence[str],
    source_map: Mapping[str, models.Chunk],
) -> list[models.Chunk]:
    chunks: list[models.Chunk] = []
    for sid in ids:
        chunk = source_map.get(sid)
        if not chunk:
            continue
        chunks.append(chunk)
    return chunks


def _extract_explanation(sections: dict[str, str]) -> str:
    for key in ("explicacao", "raciocinio correto"):
        if key in sections:
            return sections[key]
    return ""


def _extract_status(
    sections: dict[str, str],
    is_correct: bool,
) -> Literal["correct", "incorrect", "partially_correct"]:
    """Map the LLM's 'Avaliacao' section to a status enum value."""
    text = sections.get("avaliacao", "").strip().lower()
    if text.startswith("parcialmente correto"):
        return "partially_correct"
    if text.startswith("correto"):
        return "correct"
    # If LLM didn't produce a recognisable keyword, fall back to the boolean
    return "correct" if is_correct else "incorrect"


def _extract_evaluation_summary(sections: dict[str, str]) -> str | None:
    """Return the summary sentence(s) from the 'Avaliacao' section.

    The first word/phrase is the status keyword (correto/incorreto/parcialmente correto).
    Everything after the status keyword is the human-readable summary.
    """
    text = sections.get("avaliacao", "").strip()
    if not text:
        return None
    # Strip leading status keyword (up to first period or newline that follows it)
    for prefix in ("Parcialmente correto", "parcialmente correto", "Incorreto", "incorreto", "Correto", "correto"):
        if text.lower().startswith(prefix.lower()):
            rest = text[len(prefix):].lstrip(" .,:-")
            return rest.strip() if rest.strip() else None
    return text  # fallback: return as-is if no keyword matched


def _extract_related_concepts(sections: dict[str, str]) -> list[str]:
    """Parse 'Conceitos relacionados' section into a clean list."""
    text = sections.get("conceitos relacionados", "")
    if not text:
        return []
    # Normalise: replace newlines with commas, split
    text = text.replace("\n", ",")
    items = [item.strip().lstrip("- ").strip() for item in text.split(",")]
    return [item for item in items if item][:8]


def _extract_study_suggestion(sections: dict[str, str]) -> str | None:
    return sections.get("sugestao de estudo") or None


def _extract_student_feedback(sections: dict[str, str]) -> str | None:
    return sections.get("mensagem para o aluno") or None




def _extract_misconception(sections: dict[str, str]) -> str | None:
    for key in ("erro conceitual do aluno", "erro conceitual", "possivel confusao", "raciocinio simulado"):
        if key in sections:
            return sections[key]
    return None


def _extract_main_physical_concept(sections: dict[str, str]) -> str | None:
    """Extract the main physical concept identified by the LLM."""
    return sections.get("conceito fisico principal") or None


def _extract_why_wrong(sections: dict[str, str]) -> str | None:
    """Extract the explanation of why the selected answer is wrong."""
    return sections.get("por que a resposta esta errada") or None


def _extract_tip(sections: dict[str, str]) -> str | None:
    return sections.get("dica") or sections.get("dicas")


def _extract_study_text(sections: dict[str, str]) -> str | None:
    return sections.get("onde estudar no livro")


def _extract_similar_exercise(
    sections: dict[str, str],
    exercise_map: dict[str, models.Chunk],
    exercise_chunks: Sequence[models.Chunk],
    theory_chunks: Sequence[models.Chunk] | None = None,
    source_map: dict[str, models.Chunk] | None = None,
) -> "SimilarExercise | None":
    from app.schemas import SimilarExercise

    text = sections.get("exercicio similar") or sections.get("questao similar") or ""

    cited_eids = re.findall(r"\bE\d+\b", text)
    for eid in cited_eids:
        chunk = exercise_map.get(eid)
        if chunk:
            description = re.sub(r"\([ES]\d+\)", "", text).strip() or None
            return SimilarExercise(filename=chunk.filename, page=chunk.page, description=description)

    if exercise_chunks:
        chunk = exercise_chunks[0]
        description = text if text and "nenhum" not in text.lower() else None
        return SimilarExercise(filename=chunk.filename, page=chunk.page, description=description)

    if source_map and text:
        cited_sids = re.findall(r"\bS\d+\b", text)
        for sid in cited_sids:
            chunk = source_map.get(sid)
            if chunk:
                description = re.sub(r"\([ES]\d+\)", "", text).strip() or None
                return SimilarExercise(filename=chunk.filename, page=chunk.page, description=description)

    if theory_chunks:
        chunk = theory_chunks[0]
        return SimilarExercise(
            filename=chunk.filename,
            page=chunk.page,
            description=(
                "Nenhum exercicio foi localizado automaticamente, mas voce pode "
                "encontrar problemas semelhantes proximo a esta pagina no material."
            ),
        )

    return None
