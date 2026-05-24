"""Chunk relevance filtering via LLM scoring.

Before passing retrieved chunks to the feedback prompt, each chunk is scored
for conceptual relevance to the question. Only chunks scoring above the
configured threshold are included.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

from app import models
from app.core.config import get_settings

logger = logging.getLogger(__name__)

RELEVANCE_FILTER_PROMPT = """\
You are evaluating whether retrieved textbook chunks are relevant to solving a Physics 1 question.

Question:
{question_statement}

Retrieved chunks:
{chunks_block}

For EACH chunk, evaluate its relevance from 0 to 5.

Criteria:
- Does the chunk explain the same physical concept required by the question?
- Does it help justify the correct answer?
- Does it help explain the student's mistake?
- Is it directly connected to the situation described in the question?
- Would citing this chunk be pedagogically useful?

Scale:
0 - Irrelevant
1 - Very weak relation
2 - Somewhat related, but not useful for solving
3 - Related concept, but incomplete
4 - Relevant and useful
5 - Directly relevant and sufficient

Return ONLY valid JSON — an array of objects, one per chunk:
[
  {{"id": "S1", "score": 4, "reason": "short explanation"}},
  {{"id": "S2", "score": 1, "reason": "short explanation"}}
]
"""


@dataclass
class ScoredChunk:
    chunk: models.Chunk
    source_id: str
    score: int
    reason: str


def filter_chunks_by_relevance(
    llm,
    question_statement: str,
    chunks: Sequence[models.Chunk],
    source_map: dict[str, models.Chunk],
) -> list[ScoredChunk]:
    """Score chunks for relevance and return only those above threshold.

    If the LLM call fails or parsing fails, returns all chunks unfiltered
    (graceful degradation).
    """
    settings = get_settings()

    if not chunks or not settings.feedback_enable_relevance_filter:
        return [
            ScoredChunk(chunk=c, source_id=sid, score=5, reason="filter disabled")
            for sid, c in source_map.items()
        ]

    # Build chunks block for the prompt
    chunk_lines: list[str] = []
    for sid, chunk in source_map.items():
        snippet = (chunk.text or "")[:400].replace("\n", " ")
        chunk_lines.append(f"{sid}: {snippet}")

    chunks_block = "\n\n".join(chunk_lines)

    prompt = RELEVANCE_FILTER_PROMPT.format(
        question_statement=question_statement,
        chunks_block=chunks_block,
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content="You are a relevance evaluator. Return only JSON."),
            HumanMessage(content=prompt),
        ]
        result = llm.invoke(messages)
        text = result.content if isinstance(result.content, str) else str(result.content)

        # Parse JSON from response (handle wrapped code blocks)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        scores = json.loads(text)
        if not isinstance(scores, list):
            raise ValueError("Expected JSON array")

    except Exception:
        logger.warning(
            "Relevance filter failed for question, returning all chunks unfiltered",
            exc_info=True,
        )
        return [
            ScoredChunk(chunk=c, source_id=sid, score=5, reason="filter error fallback")
            for sid, c in source_map.items()
        ]

    # Map scores back to chunks
    threshold = settings.feedback_relevance_threshold
    scored: list[ScoredChunk] = []
    score_lookup = {item.get("id", ""): item for item in scores if isinstance(item, dict)}

    for sid, chunk in source_map.items():
        item = score_lookup.get(sid, {})
        chunk_score = int(item.get("score", 0))
        reason = str(item.get("reason", ""))
        if chunk_score >= threshold:
            scored.append(ScoredChunk(chunk=chunk, source_id=sid, score=chunk_score, reason=reason))

    logger.info(
        "Relevance filter: %d/%d chunks passed (threshold=%d)",
        len(scored),
        len(source_map),
        threshold,
    )
    return scored
