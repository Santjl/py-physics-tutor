# Task: Improve Feedback Request Stability, Quota Handling, and RAG Fallbacks

## Context

This project generates AI-based feedback for physics questionnaire attempts using a RAG pipeline.

The current feedback generation flow appears to perform multiple operations per question:

1. Build a query for each question.
2. Generate embeddings for retrieval.
3. Retrieve relevant chunks from indexed documents.
4. Generate feedback with Gemini.
5. Optionally validate the generated feedback using another LLM call.

Recent logs show that the feedback request is hitting Vertex AI quota/rate limits during both embedding and generation.

Observed issues include:

- `429 RESOURCE_EXHAUSTED` during embedding requests to `text-embedding-005`.
- `429 RESOURCE_EXHAUSTED` during Gemini generation requests.
- BM25 fallback is unavailable:
  - `BM25 search unavailable, skipping`
- Some questions fall back because query embedding fails.
- The feedback validator calls the LLM and also hits quota limits.
- One question took almost 10 minutes to complete:
  - `llm.invoke.question: 587.20s`
- The API eventually returns `200 OK`, but the request is too slow and unstable.

## Goal

Refactor the feedback generation pipeline to make it more stable, quota-aware, and resilient when Vertex AI rate limits occur.

The solution should reduce unnecessary model calls, limit concurrency, add proper retry/backoff behavior, improve fallback logic, and avoid blocking the whole feedback request when one question fails.

---

# Required Improvements

## 1. Add Concurrency Limits

The current flow appears to call embeddings and LLM generation too aggressively.

Add explicit concurrency limits for:

- Embedding calls
- LLM generation calls
- LLM validation calls, if still enabled

Recommended defaults:

```python
MAX_CONCURRENT_EMBEDDINGS = 2
MAX_CONCURRENT_LLM_CALLS = 2
MAX_CONCURRENT_VALIDATIONS = 1
````

If the code is async, use `asyncio.Semaphore`.

Example:

```python
embedding_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EMBEDDINGS)
llm_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)

async def safe_embed(texts):
    async with embedding_semaphore:
        return await embedding_client.embed(texts)

async def safe_generate(messages):
    async with llm_semaphore:
        return await llm.invoke(messages)
```

If the code is sync, use a worker pool with a small `max_workers`.

---

## 2. Batch Embedding Requests

Avoid embedding one query at a time when generating feedback for multiple questions.

Current problematic pattern:

```python
for question in questions:
    query_vector = embed_query(question_query)
```

Preferred pattern:

```python
queries = [build_query(question) for question in questions]
query_vectors = embedding_client.embed(queries)
```

Then map each vector back to its corresponding question.

This reduces the number of HTTP calls and lowers the chance of hitting quota limits.

---

## 3. Treat 429 Errors as Retryable

The logs currently show this behavior:

```text
Non-retryable error during embedding: 429 RESOURCE_EXHAUSTED
Non-retryable error during generation: 429 RESOURCE_EXHAUSTED
```

This should be changed.

`429 RESOURCE_EXHAUSTED` should be treated as retryable, but with exponential backoff and jitter.

Use a retry policy similar to:

```python
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception

def is_retryable_error(ex: Exception) -> bool:
    message = str(ex)
    return (
        "429" in message
        or "RESOURCE_EXHAUSTED" in message
        or "Too Many Requests" in message
    )

@retry(
    retry=retry_if_exception(is_retryable_error),
    wait=wait_exponential_jitter(initial=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def invoke_with_retry(messages):
    return client.models.generate_content(
        model=model,
        contents=messages,
    )
```

Important:

Retry must be combined with concurrency limits.
Do not add retries without limiting concurrency, because that can make quota exhaustion worse.

---

## 4. Add a Simple Circuit Breaker

If too many quota errors happen during one feedback request, stop calling the LLM repeatedly.

Example behavior:

```python
if quota_error_count >= 3:
    disable_llm_validation = True
    use_retrieval_fallback = True
```

Suggested rules:

* If embedding quota fails repeatedly:

  * Stop embedding more questions.
  * Use BM25 fallback.
* If generation quota fails repeatedly:

  * Return partial feedback for completed questions.
  * Mark failed questions as pending or fallback-generated.
* If validation quota fails:

  * Skip LLM validation and use rule-based validation.

---

## 5. Replace LLM Validation with Rule-Based Validation by Default

The current feedback validator performs another LLM call, which increases quota usage significantly.

Change the default validation strategy to a lightweight rule-based validator.

Example:

```python
def validate_feedback_basic(text: str) -> bool:
    if not text or len(text.strip()) < 300:
        return False

    required_sections = [
        "Conceito fisico principal",
        "Avaliacao",
        "Explicacao",
    ]

    normalized = text.lower()

    return all(section.lower() in normalized for section in required_sections)
```

LLM-based validation should only run when:

* The feedback is empty.
* The feedback is too short.
* The feedback does not follow the expected structure.
* A debug flag is enabled.
* A manual quality-review mode is enabled.

Suggested config:

```python
ENABLE_LLM_VALIDATION = False
```

---

## 6. Fix BM25 Fallback

The logs repeatedly show:

```text
BM25 search unavailable, skipping
```

This fallback must be fixed.

Expected retrieval behavior:

```text
Embedding available:
    use vector search + BM25, then merge/rerank results

Embedding unavailable:
    use BM25 only

BM25 unavailable:
    use metadata-based fallback or return a clear retrieval warning
```

Add clear logs for each path:

```python
logger.info("Retrieval mode: vector + BM25")
logger.warning("Embedding unavailable, using BM25-only retrieval")
logger.warning("BM25 unavailable, using metadata fallback")
```

---

## 7. Avoid Blocking the Whole Feedback Request on One Question

Currently, one question can delay the whole request for several minutes.

Add a timeout per question.

Example:

```python
QUESTION_FEEDBACK_TIMEOUT_SECONDS = 60
```

Expected behavior:

* If one question exceeds the timeout, return fallback feedback for that question.
* Continue processing other questions.
* The whole request should not wait indefinitely.

Fallback example:

```python
{
    "question_id": question.id,
    "status": "partial",
    "feedback": "Não foi possível gerar um feedback completo para esta questão neste momento. Tente novamente mais tarde.",
    "retrieval_status": "failed_or_timeout"
}
```

---

## 8. Return Partial Results Instead of Failing the Whole Request

If some questions succeed and others fail, the API should still return successful feedbacks.

Expected response model:

```json
{
  "attempt_id": 1,
  "status": "partial_success",
  "feedbacks": [
    {
      "question_id": 1,
      "status": "success",
      "feedback": "..."
    },
    {
      "question_id": 9,
      "status": "fallback",
      "feedback": "..."
    }
  ],
  "errors": [
    {
      "question_id": 9,
      "type": "quota_exceeded",
      "message": "Embedding quota exceeded. Used fallback retrieval."
    }
  ]
}
```

---

## 9. Improve Logging

Add structured logs for each stage:

* Attempt ID
* Question ID
* Retrieval mode
* Number of chunks retrieved
* Number of chunks accepted by relevance filter
* Whether fallback was used
* Embedding duration
* LLM duration
* Validation duration
* Total question duration
* Quota error count

Example:

```python
logger.info(
    "feedback.question.completed",
    extra={
        "attempt_id": attempt_id,
        "question_id": question_id,
        "fallback": fallback_used,
        "retrieval_mode": retrieval_mode,
        "chunks_retrieved": len(chunks),
        "chunks_accepted": accepted_count,
        "duration_seconds": duration,
    },
)
```

---

# Suggested Implementation Order

## Step 1

Disable LLM validation by default.

```python
ENABLE_LLM_VALIDATION = False
```

Use rule-based validation instead.

## Step 2

Add concurrency limits for embeddings and LLM calls.

## Step 3

Add retry with exponential backoff for `429 RESOURCE_EXHAUSTED`.

## Step 4

Fix BM25 fallback so retrieval does not depend only on embeddings.

## Step 5

Batch embedding queries for all questions in the attempt.

## Step 6

Add per-question timeout and partial result support.

## Step 7

Improve logs and add clear metrics for debugging.

---

# Acceptance Criteria

The implementation is complete when:

* A feedback request with 8–10 questions does not trigger uncontrolled parallel model calls.
* `429 RESOURCE_EXHAUSTED` is retried with exponential backoff.
* LLM validation is disabled by default or only used conditionally.
* BM25 fallback works when embedding fails.
* A single failed question does not block the entire feedback request.
* The API can return partial feedback results.
* Logs clearly show which questions used fallback and why.
* The full feedback request does not hang for several minutes because of one question.
* The code remains configurable through environment variables.

---

# Suggested Environment Variables

```env
MAX_CONCURRENT_EMBEDDINGS=2
MAX_CONCURRENT_LLM_CALLS=2
MAX_CONCURRENT_VALIDATIONS=1
ENABLE_LLM_VALIDATION=false
QUESTION_FEEDBACK_TIMEOUT_SECONDS=60
MAX_QUOTA_ERRORS_PER_ATTEMPT=3
ENABLE_BM25_FALLBACK=true
```

---

# Notes

Do not remove the RAG flow.

The goal is not to simplify the system by skipping retrieval.
The goal is to make retrieval and feedback generation resilient when external AI providers are slow, rate-limited, or temporarily unavailable.

Prioritize correctness, graceful degradation, and stable user experience over maximum parallel performance.

```
