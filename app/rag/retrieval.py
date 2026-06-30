from __future__ import annotations

import logging
from typing import List, Sequence

import numpy as np
from sqlalchemy import select, func, cast, literal, literal_column, column
from sqlalchemy.orm import Session
from pgvector.sqlalchemy import Vector

from app import models
from app.core.config import get_settings
from app.rag.google_client import GoogleGenAIEmbeddingClient

logger = logging.getLogger(__name__)


class GeminiEmbeddings:
    """Embedding wrapper using Google Gemini embeddings."""

    def __init__(self) -> None:
        self.client = GoogleGenAIEmbeddingClient()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self.client.embed(texts)

    def embed_query(self, text: str) -> List[float]:
        return self.client.embed([text])[0]



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _semantic_search(
    db: Session,
    query_vec: list[float],
    limit: int,
    max_distance: float = 1.0,
) -> list[tuple[int, int]]:
    """Vector similarity search. Returns [(chunk_id, rank)] with 1-indexed ranks.

    Chunks with cosine distance > *max_distance* are excluded.
    """
    dim = models.EmbeddingType.dimensions
    distance_expr = func.cosine_distance(
        models.Chunk.embedding,
        cast(query_vec, Vector(dim)),
    )
    rows = db.execute(
        select(models.Chunk.id)
        .where(distance_expr <= max_distance)
        .order_by(distance_expr)
        .limit(limit)
    ).all()
    return [(row[0], rank + 1) for rank, row in enumerate(rows)]


def _bm25_search(
    db: Session,
    query: str,
    limit: int,
    fts_config: str,
) -> list[tuple[int, int]]:
    """Full-text search via PostgreSQL tsvector. Returns [(chunk_id, rank)]."""
    try:
        savepoint = db.begin_nested()
        ts_query = func.plainto_tsquery(literal(fts_config), query)
        ts_rank = func.ts_rank(column("text_search"), ts_query)
        rows = db.execute(
            select(models.Chunk.id)
            .where(column("text_search").op("@@")(ts_query))
            .order_by(ts_rank.desc())
            .limit(limit)
        ).all()
        savepoint.commit()
        logger.info("Retrieval mode: BM25 available (%d hits)", len(rows))
        return [(row[0], rank + 1) for rank, row in enumerate(rows)]
    except Exception as exc:
        savepoint.rollback()
        logger.warning("BM25 search unavailable, skipping: %s", exc)
        return []


def reciprocal_rank_fusion(
    semantic_ranks: list[tuple[int, int]],
    bm25_ranks: list[tuple[int, int]],
    semantic_weight: float,
    bm25_weight: float,
    k: int = 60,
) -> list[tuple[int, float]]:
    """Weighted Reciprocal Rank Fusion. Returns [(chunk_id, fused_score)] sorted desc."""
    scores: dict[int, float] = {}
    for chunk_id, rank in semantic_ranks:
        scores[chunk_id] = scores.get(chunk_id, 0.0) + semantic_weight / (k + rank)
    for chunk_id, rank in bm25_ranks:
        scores[chunk_id] = scores.get(chunk_id, 0.0) + bm25_weight / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def mmr_rerank(
    chunks: list[models.Chunk],
    query_vec: list[float],
    fused_scores: dict[int, float],
    mmr_lambda: float,
    top_k: int,
) -> list[models.Chunk]:
    """Maximal Marginal Relevance re-ranking for diversity."""
    if not chunks or top_k <= 0:
        return []
    if len(chunks) <= top_k:
        return chunks

    max_score = max(fused_scores.values()) if fused_scores else 1.0

    # Precompute normalised embeddings
    query_np = np.array(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(query_np)
    if q_norm > 0:
        query_np = query_np / q_norm

    embed_cache: dict[int, np.ndarray] = {}
    for chunk in chunks:
        raw = chunk.embedding if chunk.embedding is not None else [0.0] * len(query_vec)
        emb = np.array(raw, dtype=np.float32)
        norm = np.linalg.norm(emb)
        embed_cache[chunk.id] = emb / norm if norm > 0 else emb

    selected: list[models.Chunk] = []
    candidates = list(chunks)

    for _ in range(top_k):
        if not candidates:
            break

        best_chunk = None
        best_mmr = -float("inf")

        for chunk in candidates:
            relevance = fused_scores.get(chunk.id, 0.0) / max_score if max_score > 0 else 0.0
            max_sim = 0.0
            emb_c = embed_cache[chunk.id]
            for sel in selected:
                sim = float(np.dot(emb_c, embed_cache[sel.id]))
                if sim > max_sim:
                    max_sim = sim

            score = mmr_lambda * relevance - (1.0 - mmr_lambda) * max_sim
            if score > best_mmr:
                best_mmr = score
                best_chunk = chunk

        if best_chunk is not None:
            selected.append(best_chunk)
            candidates.remove(best_chunk)

    return selected


# ---------------------------------------------------------------------------
# Chunk-type filtering
# ---------------------------------------------------------------------------

def _filter_by_chunk_type(
    db: Session,
    base_stmt,
    top_k: int,
) -> Sequence[models.Chunk]:
    def _run(chunk_type: str) -> Sequence[models.Chunk]:
        stmt = base_stmt.where(models.Chunk.chunk_type == chunk_type).limit(top_k)
        return db.scalars(stmt).all()

    results = _run("theory")
    if results:
        return results
    return _run("unknown")


def _filter_chunks_by_type(chunks: list[models.Chunk]) -> list[models.Chunk]:
    """In-memory chunk-type filtering: prefer theory, fall back to unknown."""
    theory = [c for c in chunks if c.chunk_type == "theory"]
    if theory:
        return theory
    return [c for c in chunks if c.chunk_type != "exercise"]


def _filter_exercise_chunks(chunks: list[models.Chunk]) -> list[models.Chunk]:
    """In-memory chunk-type filtering: keep only exercise chunks."""
    return [c for c in chunks if c.chunk_type == "exercise"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def embed_query(query: str) -> list[float]:
    """Embed a query string via configured provider, normalising to DB dimensions."""
    settings = get_settings()
    provider = settings.embed_provider.lower().strip()
    if provider == "ollama":
        raise RuntimeError("Ollama embeddings are not enabled in production. Set EMBED_PROVIDER=google.")
    if provider != "google":
        raise RuntimeError(f"Unsupported EMBED_PROVIDER={settings.embed_provider!r}. Set EMBED_PROVIDER=google.")

    vec = GeminiEmbeddings().embed_query(query)
    if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
        raise RuntimeError("Embedding response is not a list of numbers")
    dim = models.EmbeddingType.dimensions
    if len(vec) != dim:
        vec = (vec + [0.0] * dim)[:dim]
    return vec


def embed_queries(queries: list[str]) -> list[list[float]]:
    """Batch embed query strings via configured provider, normalized to DB dimensions."""
    if not queries:
        return []

    settings = get_settings()
    provider = settings.embed_provider.lower().strip()
    if provider == "ollama":
        raise RuntimeError("Ollama embeddings are not enabled in production. Set EMBED_PROVIDER=google.")
    if provider != "google":
        raise RuntimeError(f"Unsupported EMBED_PROVIDER={settings.embed_provider!r}. Set EMBED_PROVIDER=google.")

    vecs = GeminiEmbeddings().embed_documents(queries)

    dim = models.EmbeddingType.dimensions
    normalized: list[list[float]] = []
    for vec in vecs:
        if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
            raise RuntimeError("Embedding response is not a list of numbers")
        if len(vec) != dim:
            vec = (vec + [0.0] * dim)[:dim]
        normalized.append(vec)
    return normalized


def _hybrid_retrieve(
    db: Session,
    query_vec: list[float],
    query: str,
    candidate_count: int,
) -> tuple[list[models.Chunk], dict[int, float]]:
    """Run semantic search + BM25 + RRF, return (candidate_chunks, fused_scores).

    This is the shared core used by both retrieve_chunks and retrieve_exercise_chunks.
    The caller is responsible for chunk-type filtering and MMR re-ranking.
    """
    settings = get_settings()

    semantic_ranks = _semantic_search(db, query_vec, candidate_count, settings.retrieval_max_distance)
    bm25_ranks = _bm25_search(db, query, candidate_count, settings.retrieval_fts_config)

    fused = reciprocal_rank_fusion(
        semantic_ranks,
        bm25_ranks,
        settings.retrieval_semantic_weight,
        settings.retrieval_bm25_weight,
        settings.retrieval_rrf_k,
    )
    fused_scores = dict(fused)

    candidate_ids = [chunk_id for chunk_id, _ in fused[:candidate_count]]
    if not candidate_ids:
        return [], fused_scores

    candidate_chunks = list(
        db.scalars(select(models.Chunk).where(models.Chunk.id.in_(candidate_ids))).all()
    )
    return candidate_chunks, fused_scores


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retrieve_chunks(
    db: Session,
    query: str,
    top_k: int = 8,
    query_vec: list[float] | None = None,
) -> Sequence[models.Chunk]:
    """Retrieve theory/unknown chunks relevant to *query*.

    Pass a pre-computed *query_vec* to avoid a redundant embedding call when
    the same query is used for multiple retrieval paths (e.g. per-question).
    """
    settings = get_settings()

    if db.bind and db.bind.dialect.name == "postgresql":
        has_chunks = db.scalar(select(models.Chunk.id).limit(1))
        if not has_chunks:
            return []

        if query_vec is None:
            try:
                query_vec = embed_query(query)
            except Exception:
                logger.exception("Falling back: failed to embed query for retrieval")
                base_stmt = select(models.Chunk).order_by(models.Chunk.id)
                return _filter_by_chunk_type(db, base_stmt, top_k)

        candidate_count = top_k * settings.retrieval_candidate_multiplier
        candidate_chunks, fused_scores = _hybrid_retrieve(db, query_vec, query, candidate_count)

        pool = _filter_chunks_by_type(candidate_chunks)
        if not pool:
            return []

        return mmr_rerank(pool, query_vec, fused_scores, settings.retrieval_mmr_lambda, top_k)

    # Fallback for tests (SQLite): return earliest chunks for determinism
    base_stmt = select(models.Chunk).order_by(models.Chunk.id)
    return _filter_by_chunk_type(db, base_stmt, top_k)


def retrieve_exercise_chunks(
    db: Session,
    query: str,
    top_k: int = 2,
    query_vec: list[float] | None = None,
) -> Sequence[models.Chunk]:
    """Retrieve exercise-type chunks relevant to *query*.

    Pass a pre-computed *query_vec* to avoid a redundant embedding call when
    the same query is used for multiple retrieval paths (e.g. per-question).
    """
    settings = get_settings()

    if db.bind and db.bind.dialect.name == "postgresql":
        has_chunks = db.scalar(
            select(models.Chunk.id).where(models.Chunk.chunk_type == "exercise").limit(1)
        )
        if not has_chunks:
            return []

        if query_vec is None:
            try:
                query_vec = embed_query(query)
            except Exception:
                logger.exception("Failed to embed query for exercise retrieval")
                return []

        candidate_count = top_k * settings.retrieval_candidate_multiplier
        candidate_chunks, fused_scores = _hybrid_retrieve(db, query_vec, query, candidate_count)

        pool = _filter_exercise_chunks(candidate_chunks)
        if not pool:
            return []

        return mmr_rerank(pool, query_vec, fused_scores, settings.retrieval_mmr_lambda, top_k)

    # Fallback for tests (SQLite)
    stmt = (
        select(models.Chunk)
        .where(models.Chunk.chunk_type == "exercise")
        .order_by(models.Chunk.id)
        .limit(top_k)
    )
    return list(db.scalars(stmt).all())
