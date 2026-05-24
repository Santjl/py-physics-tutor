# Google RAG Implementation Plan

## Objective

Adapt the current API so it can use Google Cloud Agent Search / Vertex AI Search as the retrieval layer for feedback generation, without removing the current local RAG flow.

The switch must be controlled by `.env`, so the application can run in two modes:

```env
RAG_PROVIDER=local
```

or

```env
RAG_PROVIDER=google_agent_search
```

The current local chunk-based implementation stays intact as the default fallback.

---

## Current API Structure

### Feedback entrypoint

- Route: `POST /attempts/{attempt_id}/feedback`
- File: [app/api/routes/feedback.py](/C:/Projetos/py-physics-tytor/app/api/routes/feedback.py)
- Current orchestration entrypoint: `generate_feedback(db, attempt)` in [app/rag/feedback.py](/C:/Projetos/py-physics-tytor/app/rag/feedback.py)

### Current local RAG flow

- Local ingestion and chunk creation:
  - [app/rag/processing.py](/C:/Projetos/py-physics-tytor/app/rag/processing.py)
  - [app/rag/chunking.py](/C:/Projetos/py-physics-tytor/app/rag/chunking.py)
- Local retrieval:
  - [app/rag/retrieval.py](/C:/Projetos/py-physics-tytor/app/rag/retrieval.py)
- LLM generation:
  - [app/rag/google_client.py](/C:/Projetos/py-physics-tytor/app/rag/google_client.py)
  - `GoogleGenAIChatClient` already exists

### Architectural constraints discovered in the codebase

- `Settings` are centralized in [app/core/config.py](/C:/Projetos/py-physics-tytor/app/core/config.py).
- The app already supports provider-style switching for LLM and embeddings through:
  - `llm_provider`
  - `embed_provider`
- Test mode is controlled through `APP_ENV=test`.
- The current feedback response schema is oriented around attempt-based feedback, not a standalone `/feedback/generate` endpoint.

Because of that, the least disruptive implementation is:

1. Keep `POST /attempts/{attempt_id}/feedback`.
2. Introduce a RAG provider strategy inside `app/rag`.
3. Reuse Gemini generation infrastructure where possible.
4. Add Google retrieval only when `RAG_PROVIDER=google_agent_search`.

---

## Recommended Target Design

## Provider strategy

Add a retrieval provider switch in settings:

```env
RAG_PROVIDER=local
```

Allowed values:

- `local`
- `google_agent_search`

Behavior:

- `local`: keep current retrieval behavior exactly as-is.
- `google_agent_search`: skip local embedding/vector retrieval for feedback generation and retrieve context from Discovery Engine instead.

This should affect feedback retrieval only. It should not break the current document upload flow, because the user explicitly asked to keep the existing RAG implementation.

---

## Required `.env` additions

Add the following fields to [app/core/config.py](/C:/Projetos/py-physics-tytor/app/core/config.py):

```env
RAG_PROVIDER=local
GOOGLE_CLOUD_PROJECT_ID=
GOOGLE_CLOUD_LOCATION=global
GOOGLE_CLOUD_GEMINI_LOCATION=us-central1
GOOGLE_DISCOVERY_ENGINE_ID=
GOOGLE_DISCOVERY_DATA_STORE_ID=
GOOGLE_DISCOVERY_SERVING_CONFIG=default_search
GOOGLE_APPLICATION_CREDENTIALS=
GEMINI_MODEL=gemini-1.5-flash
GOOGLE_AGENT_SEARCH_PAGE_SIZE=5
```

Recommended `Settings` fields:

- `rag_provider: str = "local"`
- `google_cloud_project_id: str | None = None`
- `google_cloud_location: str = "global"`
- `google_cloud_gemini_location: str = "us-central1"`
- `google_discovery_engine_id: str | None = None`
- `google_discovery_data_store_id: str | None = None`
- `google_discovery_serving_config: str = "default_search"`
- `google_application_credentials: str | None = None`
- `gemini_model: str = "gemini-1.5-flash"`
- `google_agent_search_page_size: int = 5`

Notes:

- Keep existing `google_cloud_project` and `google_chat_model` temporarily if other flows still use them.
- Do not silently overload existing fields with a second meaning.
- If desired, later unify:
  - `google_cloud_project` -> `google_cloud_project_id`
  - `google_chat_model` -> `gemini_model`

For now, backward compatibility is more important than cleanup.

---

## New internal abstraction

Introduce a provider boundary for feedback retrieval.

Suggested new file:

- `app/rag/providers.py`

Suggested types:

```python
from dataclasses import dataclass, field

@dataclass
class RetrievedContextItem:
    id: str
    title: str | None
    source_uri: str | None
    page_number: int | None
    snippet: str
    metadata: dict = field(default_factory=dict)
    rank: int | None = None


@dataclass
class RetrievalResult:
    query: str
    items: list[RetrievedContextItem]
    provider: str


class FeedbackRetrievalProvider(Protocol):
    def retrieve_for_answer(self, db, ans: models.Answer) -> RetrievalResult: ...
```

Why this matters:

- `app/rag/feedback.py` is currently tightly coupled to SQLAlchemy chunk retrieval.
- Google Agent Search should not leak SDK objects into the rest of the pipeline.
- A normalized internal structure makes it possible to preserve the current local flow and add the Google flow cleanly.

---

## Local provider

Wrap the current behavior instead of rewriting it.

Suggested file:

- `app/rag/local_retrieval_provider.py`

Responsibilities:

- Reuse `_retrieval_query_for_answer()`
- Reuse `retrieve_chunks()` and `retrieve_exercise_chunks()`
- Convert `models.Chunk` into `RetrievedContextItem`
- Preserve exercise retrieval separately if still needed

Suggested normalization for local chunks:

- `id`: chunk id as string
- `title`: `chunk.filename`
- `source_uri`: `None`
- `page_number`: `chunk.page`
- `snippet`: `chunk.text[:1000]`
- `metadata`: include:
  - `chunk_type`
  - `document_id`
  - `filename`

This provider becomes the default path when `RAG_PROVIDER=local`.

---

## Google Agent Search provider

Suggested new file:

- `app/rag/google_agent_search.py`

Suggested service:

```python
class GoogleAgentSearchService:
    def search_relevant_context(self, query: str, page_size: int) -> list[RetrievedContextItem]:
        ...
```

### Responsibilities

- Build the Discovery Engine serving config path
- Execute the search
- Normalize results into `RetrievedContextItem`
- Log query and number of results
- Never expose raw Discovery Engine response objects outside the service

### Serving config path logic

Support both resource styles.

Engine-based:

```txt
projects/{project_id}/locations/{location}/collections/default_collection/engines/{engine_id}/servingConfigs/{serving_config}
```

DataStore-based:

```txt
projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{data_store_id}/servingConfigs/{serving_config}
```

Resolution rule:

- If `GOOGLE_DISCOVERY_ENGINE_ID` is set, prefer engine-based path.
- Else if `GOOGLE_DISCOVERY_DATA_STORE_ID` is set, use data-store path.
- Else raise a configuration error.

### Search query builder

Suggested helper:

- `build_agent_search_query(question, topic, correct_answer, alternatives, student_answer=None)`

Recommended composition:

1. Topic first
2. Question statement
3. Correct answer text
4. Key alternative texts
5. Optional student answer only if it adds useful wording

Example:

```txt
Topic: Leis de Newton
Question: Um bloco de massa m sofre forca resultante F...
Correct answer: A aceleracao e diretamente proporcional a forca resultante.
Alternatives: inercia; aceleracao; massa; forca resultante
```

Do not over-engineer keyword extraction in the first version. Deterministic concatenation is enough and easier to test.

### Result normalization

Map each Discovery Engine hit into:

```json
{
  "id": "string",
  "title": "string",
  "sourceUri": "string",
  "pageNumber": 12,
  "snippet": "string",
  "metadata": {}
}
```

Recommended source mapping heuristics:

- `id`: document id or derived result identifier
- `title`: document title, filename, or `derived_struct_data.title`
- `source_uri`: `link`, `uri`, or GCS path when present
- `page_number`: page metadata when present
- `snippet`: extracted snippet or summary text
- `metadata`: preserve remaining safe fields useful for debugging

The mapping function must be isolated and unit tested because the Discovery Engine payload shape is the highest-risk integration point.

---

## Gemini feedback service for Google flow

The repository already has Gemini client infrastructure in [app/rag/google_client.py](/C:/Projetos/py-physics-tytor/app/rag/google_client.py), but the current feedback generation format is section-based and optimized for the local per-question parser.

For the Google retrieval flow, add a dedicated JSON-oriented generation service instead of forcing the existing parser to absorb two prompt contracts.

Suggested file:

- `app/rag/gemini_feedback_service.py`

Suggested API:

```python
class GeminiFeedbackService:
    def generate_question_feedback(
        self,
        *,
        question: str,
        alternatives: list[dict],
        student_answer: str,
        correct_answer: str,
        topic: str | None,
        retrieved_context: list[RetrievedContextItem],
    ) -> dict:
        ...
```

### Responsibilities

- Build a structured prompt
- Enforce Brazilian Portuguese
- Call Gemini through Vertex AI
- Parse JSON
- Validate required fields
- Return normalized feedback payload

### Prompt guidance

Use a prompt based on `rag_improvement.md`, but keep it short enough to avoid oversized requests.

Required instructions:

- Act as a Physics tutor
- Output only valid JSON
- Write in Brazilian Portuguese
- Use retrieved context as the primary source
- Do not invent citations, page numbers, or filenames
- State when context is insufficient
- Be concise and pedagogical

### JSON parsing

Add a small parser helper:

- Strip Markdown code fences if present
- Parse JSON
- Validate required keys
- Raise a domain-specific error on invalid output

This is important because the current codebase does not yet have a structured JSON parser for Gemini output in the feedback path.

---

## How to integrate without removing the current flow

## Minimal integration point

Modify [app/rag/feedback.py](/C:/Projetos/py-physics-tytor/app/rag/feedback.py), but keep the existing local orchestration available.

Recommended refactor:

1. Keep current local implementation in place.
2. Add a new top-level branch inside `generate_feedback`.
3. Route by `settings.rag_provider`.

Suggested shape:

```python
def generate_feedback(db, attempt):
    settings = get_settings()

    if settings.rag_provider == "google_agent_search":
        return generate_feedback_google(db, attempt)

    return generate_feedback_local(db, attempt)
```

Then:

- Move existing current implementation into `generate_feedback_local`
- Add `generate_feedback_google`

This is the safest change because it preserves behavior for current users and tests.

---

## Google feedback orchestration

Suggested new function:

- `generate_feedback_google(db, attempt)`

### Per-question flow

For each incorrect answer:

1. Build a retrieval query from the question data.
2. Call `GoogleAgentSearchService.search_relevant_context`.
3. Call `GeminiFeedbackService.generate_question_feedback`.
4. Convert the JSON output into the existing `PerQuestionFeedback` schema.
5. Aggregate into the existing `FeedbackResponse`.

### Important compatibility decision

Do not introduce a brand-new API response format for the existing endpoint.

Instead, map the new Google JSON output into the current schema:

- `summary` -> `evaluation_summary` or `student_feedback`
- `studentMisconception` -> `misconception`
- `correctExplanation` -> `explanation` and `correct_reasoning`
- `studyRecommendation` -> `study_suggestion`
- `references` -> `study` and `global_references`

That preserves the endpoint contract already used by the frontend and tests.

### Empty retrieval behavior

If Agent Search returns zero documents:

- still call Gemini
- pass an empty retrieved context list
- explicitly instruct the model that no supporting material was found
- mark the resulting recommendation conservatively

This matches the requirement from `rag_improvement.md`.

---

## Proposed file changes

### Files to update

- [app/core/config.py](/C:/Projetos/py-physics-tytor/app/core/config.py)
  - add `RAG_PROVIDER`
  - add Google Discovery Engine settings
  - add Gemini model/location settings for the new flow

- [app/rag/feedback.py](/C:/Projetos/py-physics-tytor/app/rag/feedback.py)
  - split local and Google feedback orchestration
  - keep current local path intact

- [README.md](/C:/Projetos/py-physics-tytor/README.md)
  - add `.env` example for `RAG_PROVIDER`
  - explain Google prerequisites

### Files to add

- `app/rag/providers.py`
- `app/rag/local_retrieval_provider.py`
- `app/rag/google_agent_search.py`
- `app/rag/gemini_feedback_service.py`
- `tests/test_google_agent_search.py`
- `tests/test_gemini_feedback_service.py`
- `tests/test_rag_provider_switch.py`

Optional:

- `app/rag/errors.py`
  - define `GoogleRAGConfigurationError`
  - define `GoogleRAGRetrievalError`
  - define `GeminiJSONError`

---

## Discovery Engine client notes

Use the official Google Cloud Discovery Engine client library, not raw HTTP.

Expected package direction:

- `google-cloud-discoveryengine`

Typical usage will likely involve:

```python
from google.cloud import discoveryengine_v1 as discoveryengine
```

and a `SearchServiceClient`.

The exact SDK field names should be validated during implementation because Discovery Engine result payloads vary depending on configuration and document schema. The normalization layer should absorb that variability.

---

## Logging requirements

Add logs for:

- selected `rag_provider`
- generated search query
- Discovery Engine serving config path
- number of documents retrieved
- Gemini model used
- success/failure of feedback generation

Do not log:

- credentials
- full document payloads
- oversized prompts

Current logging style is already centralized enough to keep this consistent.

---

## Error handling design

### Configuration errors

Raise a clear error when:

- `RAG_PROVIDER=google_agent_search` and `GOOGLE_CLOUD_PROJECT_ID` is missing
- both `GOOGLE_DISCOVERY_ENGINE_ID` and `GOOGLE_DISCOVERY_DATA_STORE_ID` are missing
- the credentials path is missing or unreadable when required

### Retrieval errors

If Discovery Engine fails:

- log the exception
- generate fallback feedback with no sources if possible
- do not crash the entire request unless the current application error policy requires it

### Generation errors

If Gemini returns invalid JSON:

- retry once if practical
- otherwise fall back to a safe, minimal feedback structure

This mirrors the resilience already present in the current local feedback flow.

---

## Testing strategy

Add unit tests with mocks only.

### 1. Query builder

File:

- `tests/test_google_agent_search.py`

Cases:

- includes topic and question statement
- includes correct answer
- behaves correctly when alternatives are absent

### 2. Serving config path builder

Cases:

- engine-based path
- data-store-based path
- invalid configuration

### 3. Discovery Engine mapping

Cases:

- maps title, uri, snippet, page number
- handles missing optional fields
- preserves rank ordering

### 4. Gemini prompt builder

File:

- `tests/test_gemini_feedback_service.py`

Cases:

- prompt contains Portuguese requirement
- prompt includes question, answer, and retrieved context
- prompt handles empty retrieval cleanly

### 5. Invalid Gemini JSON

Cases:

- fenced JSON
- malformed JSON
- missing required keys

### 6. Provider switch

File:

- `tests/test_rag_provider_switch.py`

Cases:

- `RAG_PROVIDER=local` keeps current path
- `RAG_PROVIDER=google_agent_search` uses Google path

### 7. Endpoint contract preservation

Update existing feedback tests only if necessary so they continue to validate the same `FeedbackResponse` shape.

The user specifically asked not to remove the current flow, so regression coverage here is important.

---

## Rollout plan

## Phase 1

- Add settings
- Add provider boundary
- Refactor current local path behind `generate_feedback_local`
- No behavior change

## Phase 2

- Add `GoogleAgentSearchService`
- Add mapping tests
- Add query builder tests

## Phase 3

- Add `GeminiFeedbackService`
- Add JSON parsing and validation
- Add empty-context fallback handling

## Phase 4

- Wire `generate_feedback_google`
- Preserve `FeedbackResponse` contract
- Update docs and `.env.example` if present

---

## Recommended implementation details

### Do not remove current document upload processing

The user asked to keep the implemented RAG flow. That means:

- keep `/documents/upload`
- keep chunking/embedding/indexing pipeline
- keep local retrieval modules

Even if Google-managed retrieval becomes the preferred production mode, the local pipeline remains useful for:

- fallback
- local development
- tests
- environments without Google credentials

### Do not create a duplicate public endpoint unless required

The current API already centers feedback around attempts. Extending the internals is lower risk than adding a second public contract.

### Keep Google-specific concerns isolated

`app/rag/google_agent_search.py` should contain:

- SDK client setup
- serving config path generation
- search request execution
- response normalization

It should not know about FastAPI or SQLAlchemy route concerns.

---

## Final recommendation

The correct change for this codebase is not a full rewrite of the RAG layer. The correct change is a provider split inside `app/rag/feedback.py`, backed by:

- `RAG_PROVIDER` in `.env`
- a `GoogleAgentSearchService` for retrieval
- a dedicated `GeminiFeedbackService` for structured JSON generation
- a normalization layer that maps Google results into the current feedback schema

That preserves the existing API contract, keeps the current local implementation available, and adds the Google Cloud retrieval path in a way that is reversible and testable.
