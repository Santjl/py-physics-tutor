## Plan: RAG Pipeline Improvements

TL;DR: 9 concrete improvements across 4 phases, prioritized by impact. All changes are in `py-physics-tytor/`.

---

### Phase 1 — Security & Correctness

**Step 1 — Fix SQL injection in BM25 search** *(highest priority)*
In `_bm25_search` ([retrieval.py](py-physics-tytor/app/rag/retrieval.py#L54)), `fts_config` is string-interpolated into `literal_column(f"'{fts_config}'")` — replace with a properly bound SQLAlchemy literal so it's never injected into SQL.

**Step 2 — Fix CORS wildcard**
In [main.py](py-physics-tytor/app/main.py#L13), replace `allow_origins=["*"]` with a `cors_origins` field in `Settings` ([config.py](py-physics-tytor/app/core/config.py)), defaulting to `["*"]` for dev but narrowable via env var in production.

**Step 3 — Fix image PDF producing 0 chunks silently marked "ready"**
In `process_document_inline` ([processing.py](py-physics-tytor/app/rag/processing.py#L209)), when `chunks` is empty the document is set to `"ready"` — it should be `"failed"` with a clear status message.

---

### Phase 2 — Performance *(steps are independent)*

**Step 4 — Embed query once, reuse for both retrieval paths**
In `_retrieve_per_question` ([feedback.py](py-physics-tytor/app/rag/feedback.py#L512)), the same query string is embedded twice — once inside `retrieve_chunks` and once inside `retrieve_exercise_chunks`. Extract a shared `_embed_query()` call, then pass the cached vector into both paths.

**Step 5 — Parallelize LLM calls across questions**
In `_generate_feedback_with_llm` ([feedback.py](py-physics-tytor/app/rag/feedback.py#L649)), LLM is called sequentially per incorrect question. Refactor with `ThreadPoolExecutor(max_workers=min(n_incorrect, 4))` — each question call is independent, so they can all run in parallel.

---

### Phase 3 — Retrieval Quality

**Step 6 — Add cosine distance threshold**
In `_semantic_search` ([retrieval.py](py-physics-tytor/app/rag/retrieval.py#L35)), add a `.where(distance_expr <= max_distance)` filter (new `retrieval_max_distance: float = 0.7` setting). Prevents irrelevant chunks from polluting LLM context when no good match exists.

**Step 7 — Fix chunk classification**
In `_classify_chunk_type` ([processing.py](py-physics-tytor/app/rag/processing.py#L46)), the `"unknown"` type is currently dead for non-empty text (always returns `"theory"` as fallback). Add a `THEORY_MARKERS` list and make `"unknown"` the true default — only positively classify as `"theory"` or `"exercise"`.

---

### Phase 4 — Maintainability

**Step 8 — Remove unused `query` parameter**
Remove `query: str = "physics study tips"` from `generate_feedback` ([feedback.py](py-physics-tytor/app/rag/feedback.py#L729)) — it's never used.

**Step 9 — Deduplicate retrieval code** *(depends on Step 4)*
After Step 4 introduces `_hybrid_retrieve(db, query_vec, query, ...)`, refactor `retrieve_chunks` and `retrieve_exercise_chunks` into thin wrappers (~60 lines of duplicated code removed).

---

### Relevant Files

| File | Steps |
|------|-------|
| [app/rag/retrieval.py](py-physics-tytor/app/rag/retrieval.py) | 1, 4, 6, 9 |
| [app/rag/feedback.py](py-physics-tytor/app/rag/feedback.py) | 4, 5, 8 |
| [app/rag/processing.py](py-physics-tytor/app/rag/processing.py) | 3, 7 |
| [app/main.py](py-physics-tytor/app/main.py) | 2 |
| [app/core/config.py](py-physics-tytor/app/core/config.py) | 2, 6 |

### Verification

1. `pytest` — all existing tests must pass after each phase
2. New test: `_semantic_search` filters chunks above distance threshold
3. New test: embed is called exactly once per question in `_retrieve_per_question`
4. New test: `_generate_feedback_with_llm` invokes LLM in parallel (mock + call count check)
5. Manual: Upload image-only PDF → status should be `"failed"`, not `"ready"`

### Decisions & Exclusions

- **No OCR**: Image PDF fix (Step 3) uses `"failed"` status — full OCR is out of scope
- **No async refactor**: `ThreadPoolExecutor` (Step 5) is correct for the existing sync codebase
- **`_build_summary` topic improvements**: Requires schema changes, excluded from this plan
- **tiktoken**: Excluded — lowering `DEFAULT_MAX_TOKENS` by ~20% is a simpler, safer alternative if needed
