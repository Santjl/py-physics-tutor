# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Physics Tutor API — a FastAPI backend for creating physics questionnaires, collecting student attempts, and generating personalized feedback using RAG (Retrieval-Augmented Generation) with Gemini on Google Cloud. The codebase includes Portuguese (pt-BR) comments and content.

## Commands

### Run dev server
```bash
uvicorn app.main:app --reload
```
OpenAPI docs at `http://localhost:8000/docs`.

### Run tests
```bash
pytest                       # all tests (in-memory SQLite, APP_ENV=test)
pytest tests/test_auth.py    # single test file
pytest -k test_name          # single test by name
```

### Database migrations
```bash
alembic upgrade head          # apply all migrations
alembic revision -m "message" # create new migration
alembic downgrade -1          # rollback last migration
```

### Docker (full stack)
```bash
docker-compose up --build     # API + PostgreSQL/pgvector + Adminer (port 8080)
```

### Seed sample data
```bash
python scripts/seed_sample.py
```

## Architecture

### Layer structure
```
app/api/routes/   → HTTP endpoints (auth, questionnaires, documents, feedback, health)
app/services/     → Business logic (attempt scoring)
app/rag/          → RAG pipeline (PDF processing, chunking, retrieval, feedback generation)
app/models.py     → SQLAlchemy 2.0 ORM models (Mapped[] style)
app/schemas.py    → Pydantic v2 request/response schemas
app/core/         → Config (pydantic-settings), security (JWT/bcrypt), logging
app/db/           → Engine and session factory
```

### Key tech decisions
- **Two-database strategy**: PostgreSQL + pgvector in production; in-memory SQLite for tests. The `EmbeddingType` custom TypeDecorator in `models.py` handles vector columns across both dialects (pgvector vs JSON fallback).
- **Gemini for LLM**: Uses the `google-genai` SDK for embeddings and feedback generation. Production auth can use Vertex AI via ADC or the Gemini API via API key.
- **Test mode (`APP_ENV=test`)**: Skips real LLM calls — returns deterministic embeddings and feedback using stored chunks. Controlled via `get_settings().app_env`.
- **Background PDF processing**: `FastAPI.BackgroundTasks` for async PDF ingestion (synchronous in test mode). Documents go through states: `pending → processing → ready | failed`.

### RAG pipeline flow
1. Admin uploads PDF → `rag/processing.py` extracts text (PyMuPDF), splits into token-based chunks with overlap (`rag/chunking.py`)
2. Chunks classified as `theory`/`exercise`/`unknown` via keyword heuristics, embedded via Gemini, stored with pgvector
3. Student requests feedback → `rag/retrieval.py` finds similar theory chunks per question (cosine distance), falls back to `unknown` chunks if no theory found
4. `rag/feedback.py` builds per-question prompts with retrieved context, calls LLM, includes source citations

### Auth model
- JWT tokens (HS256) with user ID and role in payload
- Two roles: `admin` (create questionnaires, upload docs) and `student` (submit attempts, get feedback)
- Role enforcement via `require_admin()`/`require_student()` dependency functions in route handlers

### Testing patterns
- `conftest.py` sets `APP_ENV=test` before imports, creates session-scoped SQLite schema
- Each test gets its own DB transaction that rolls back after the test
- `client` fixture overrides `get_db` dependency with test session
- `admin_user` and `student_user` fixtures use password `"secret"`

## Environment Variables

Configured via `.env` file or environment (loaded by pydantic-settings):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...` | Database connection string |
| `APP_ENV` | `dev` | `dev`/`test`/`prod` — test mode disables LLM calls |
| `GOOGLE_CLOUD_PROJECT` | `None` | Google Cloud project for Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex AI region |
| `GOOGLE_GENAI_API_KEY` | `None` | Gemini API key for direct API usage |
| `GOOGLE_CHAT_MODEL` | `gemini-2.5-flash` | Model for feedback generation |
| `GOOGLE_EMBED_MODEL` | `text-embedding-005` | Model for embeddings |
| `SECRET_KEY` | `change-me` | JWT signing key |
