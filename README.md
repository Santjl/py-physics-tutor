# Physics Tutor API (Python)

FastAPI + SQLAlchemy backend to create questionnaires, collect attempts, and power RAG-based feedback with Gemini on Google Cloud. This repository will be built in small PRs; this first PR ships the scaffold, Docker setup, database migrations, and basic questionnaire CRUD.

## Stack
- FastAPI, Pydantic v2
- SQLAlchemy 2.0 + Alembic
- PostgreSQL + pgvector
- Pytest
- Gemini via Google Gen AI SDK for chat + embeddings

## Additional docs
- `RAG_PIPELINE.md`: detailed local RAG pipeline design
- `RAG_FLOWS_README.md`: separated documentation for local-chunk flow vs Google retrieval flow

## Environment
- `DATABASE_URL` (e.g. `postgresql+psycopg://postgres:postgres@localhost:5432/quiz_db`)
- `APP_ENV` = `dev|test|prod`
- `LLM_PROVIDER` = `google|ollama` (default `google`)
- `EMBED_PROVIDER` = `google|ollama` (default `google`)
- `RETRIEVAL_PROVIDER` = `local|google` (default `local`)
- `GOOGLE_CLOUD_PROJECT` for Vertex AI auth
- `GOOGLE_CLOUD_PROJECT_ID` for Discovery Engine / Agent Search resource paths
- `GOOGLE_CLOUD_LOCATION` (default `us-central1`)
- `GOOGLE_GENAI_API_KEY` for Gemini API auth when not using Vertex AI
- `GOOGLE_CHAT_MODEL` (default `gemini-2.5-flash`)
- `GOOGLE_EMBED_MODEL` (default `text-embedding-005`)
- `GOOGLE_DISCOVERY_ENGINE_ID` or `GOOGLE_DISCOVERY_DATA_STORE_ID` for Google retrieval
- `GOOGLE_DISCOVERY_SERVING_CONFIG` (default `default_search`)
- `GOOGLE_AGENT_SEARCH_PAGE_SIZE` (default `4`)
- `GOOGLE_APPLICATION_CREDENTIALS` optional path to a service-account JSON for Discovery Engine / ADC
- `OLLAMA_BASE_URL` (default `http://localhost:11434`)
- `OLLAMA_CHAT_MODEL` (default `qwen3:1.7b`)
- `OLLAMA_EMBED_MODEL` (default `nomic-embed-text`)

Provider selection:
- Default behavior uses Gemini on Google Cloud/API for both chat and embeddings.
- Retrieval defaults to local database chunks; set `RETRIEVAL_PROVIDER=google` to use Google Agent Search / Discovery Engine retrieval in the feedback endpoint.
- To keep Ollama available but not used, leave providers at default (`google`).
- To switch back to Ollama later, set `LLM_PROVIDER=ollama` and/or `EMBED_PROVIDER=ollama`.

## Local setup (host)
1. Install Python 3.11+.
2. `python -m venv .venv && . .venv/Scripts/activate` (or `source .venv/bin/activate` on Unix).
3. `pip install -r requirements.txt`
4. Set `DATABASE_URL` in a `.env` file or environment.
5. Authenticate with Google.
6. If using Vertex AI, run `gcloud auth application-default login` and set `GOOGLE_CLOUD_PROJECT`.
7. If using the Gemini API directly, set `GOOGLE_GENAI_API_KEY` instead.
8. If using Google retrieval, also set `RETRIEVAL_PROVIDER=google` plus `GOOGLE_CLOUD_PROJECT_ID` and either `GOOGLE_DISCOVERY_ENGINE_ID` or `GOOGLE_DISCOVERY_DATA_STORE_ID`.
9. If using a service account locally for Discovery Engine, set `GOOGLE_APPLICATION_CREDENTIALS`.
10. Run migrations: `alembic upgrade head`
11. Start API: `uvicorn app.main:app --reload`

OpenAPI docs live at `/docs`.

Auth (PR2):
- Register student: `POST /auth/register` with `{"email": "...", "password": "..."}`.
- Login: `POST /auth/login` (form data `username`, `password`) → bearer token.
- Admin-only endpoints: create questionnaire and add questions. Create an admin row manually or via seed in dev.
- Student-only: submit attempts; student_id is taken from the JWT.

Documents (PR3):
- Upload PDF: `POST /documents/upload` (multipart `file`) as admin. In test env, processing is synchronous; otherwise it runs in a background task.
- Check status: `GET /documents/{id}`.
- PDFs are parsed with PyMuPDF, chunked (~900 chars, 150 overlap), embedded via Gemini embeddings (`GOOGLE_EMBED_MODEL`, default `text-embedding-005`), and stored in `chunks` with pgvector.
- RAG orchestration will use LangChain in PR4.
- Each chunk is classified as `theory`, `exercise`, or `unknown` during ingestion (simple keyword heuristics) and optional chapter/section titles are stored when detected.

Feedback (PR4):
- Generate feedback: `POST /attempts/{attempt_id}/feedback` as the owning student.
- With `RETRIEVAL_PROVIDER=local`, retrieves similar chunks (pgvector) from theory-only content and prompts Gemini (`GOOGLE_CHAT_MODEL`, default `gemini-2.5-flash`). If no theory chunks are available, it can fall back to `unknown` but never to exercises.
- With `RETRIEVAL_PROVIDER=google`, sends the per-question retrieval query to Google Agent Search / Discovery Engine, adapts the returned context into the same feedback pipeline, and preserves the same API response shape for `study` and `global_references`.
- Test mode (`APP_ENV=test`) avoids LLM calls and returns deterministic feedback using stored chunks.

Hardening (PR5):
- Basic validation: questionnaire title required; questions require at least one correct option and unique letters.
- Global error guard returns 500 with logged exception; logging configured to stdout.
- CORS open for ease of local use (tighten for prod).
- Evaluation tests: scoring correctness, validation edge cases, feedback output with citations in test mode.

## Docker
`docker-compose up --build`

Notes:
- The API reads `DATABASE_URL` from the service environment.
- If you run the API in Docker, pass through either `GOOGLE_CLOUD_PROJECT` plus Google ADC credentials, or `GOOGLE_GENAI_API_KEY`.
- If you run the API on host instead of container, ensure it can reach your Postgres instance and that Google credentials are available in the environment.

## Database migrations
- Create: `alembic revision -m "message"`
- Apply: `alembic upgrade head`
- Downgrade: `alembic downgrade -1`

## Tests
`pytest`

Tests run against an in-memory SQLite database for speed; production uses Postgres/pgvector.

## Seed sample data
`python scripts/seed_sample.py`

Seeds a “Kinematics Basics” questionnaire with one question and options.
