
# Task: Adapt the API to use Google Cloud Agent Search / Vertex AI Search as the RAG retrieval layer

## Context

This project is a Physics Tutor application.

The application receives a student's answer to a physics question and generates personalized feedback using AI. Previously, the RAG pipeline may have been implemented locally or manually using custom embeddings/vector storage. Now, the system must be adapted to use Google Cloud services for the RAG layer.

The new architecture should be:

Student answer
→ Backend API
→ Google Agent Search / Vertex AI Search / Discovery Engine API
→ Retrieve relevant chunks from PDFs stored in Google Cloud Storage
→ Send question + student answer + correct answer + retrieved context to Gemini
→ Generate feedback in Brazilian Portuguese
→ Return/save the feedback

The PDFs are stored in Google Cloud Storage and already indexed through an Agent Search / Vertex AI Search app using an unstructured document Data Store.

The backend should consume the Agent Search app programmatically through the Discovery Engine API.

## Goal

Refactor or extend the current API so that feedback generation uses Google Cloud Agent Search as the retrieval mechanism and Gemini as the generation model.

The API should not manually create embeddings, split PDFs, or query a local vector database for this flow. Retrieval must be delegated to Google Agent Search / Discovery Engine.

## Expected Flow

Implement the following flow:

1. Receive a feedback generation request containing:
   - question text
   - alternatives, if available
   - student answer
   - correct answer
   - question topic or subject
   - optional questionnaire/question identifiers

2. Build a search query for Agent Search using:
   - the question topic
   - the question statement
   - relevant keywords from the alternatives or correct answer

3. Call Google Discovery Engine Search API using the configured Agent Search app or Data Store.

4. Retrieve the most relevant search results/chunks.

5. Normalize the retrieved context into a clean internal structure containing:
   - document id
   - document title or file name, when available
   - page number, when available
   - snippet/content, when available
   - source URI, when available
   - relevance/ranking position

6. Build a structured prompt for Gemini using:
   - the original question
   - the student answer
   - the correct answer
   - the retrieved context
   - strict pedagogical instructions

7. Call Gemini through Vertex AI.

8. Return a structured feedback response in Brazilian Portuguese.

## Important Requirement

All generated feedback must be written in Brazilian Portuguese, even if the internal prompts, code comments, or API configuration are written in English.

## Google Cloud Configuration

Add configuration support for the following environment variables:

```env
GOOGLE_CLOUD_PROJECT_ID=
GOOGLE_CLOUD_LOCATION=global
GOOGLE_CLOUD_GEMINI_LOCATION=us-central1
GOOGLE_DISCOVERY_ENGINE_ID=
GOOGLE_DISCOVERY_DATA_STORE_ID=
GOOGLE_DISCOVERY_SERVING_CONFIG=default_search
GOOGLE_APPLICATION_CREDENTIALS=
GEMINI_MODEL=gemini-1.5-flash
````

The implementation should support both possible Discovery Engine resource paths:

1. Engine-based path:

```txt
projects/{project_id}/locations/{location}/collections/default_collection/engines/{engine_id}/servingConfigs/{serving_config}
```

2. DataStore-based path:

```txt
projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{data_store_id}/servingConfigs/{serving_config}
```

If the project already has a configuration pattern, follow the existing style instead of creating an inconsistent configuration mechanism.

## Retrieval Service

Create or adapt a service responsible only for retrieval.

Suggested name:

```txt
GoogleAgentSearchService
```

Responsibilities:

* Build the Discovery Engine serving config path.
* Execute a search request.
* Return a list of normalized retrieved documents/chunks.
* Avoid leaking Google-specific response objects to the rest of the application.
* Log enough information for debugging, but never log credentials.

Suggested method:

```txt
searchRelevantContext(query, pageSize)
```

Expected return structure:

```json
[
  {
    "id": "string",
    "title": "string",
    "sourceUri": "string",
    "pageNumber": "number|null",
    "snippet": "string",
    "metadata": {}
  }
]
```

## Gemini Feedback Service

Create or adapt a service responsible for feedback generation.

Suggested name:

```txt
GeminiFeedbackService
```

Responsibilities:

* Receive the question, student answer, correct answer, and retrieved context.
* Build the Gemini prompt.
* Call Gemini through Vertex AI.
* Return a structured feedback object.

The prompt must instruct Gemini to:

* Act as a Physics tutor.
* Generate feedback in Brazilian Portuguese.
* Use the retrieved context as the main source.
* Avoid hallucinating citations or page numbers.
* Clearly explain the student's likely misconception.
* Explain the correct reasoning.
* Recommend where to study using the retrieved document references when available.
* If the retrieved context is insufficient, explicitly say that the material retrieved was not enough to fully justify the answer.
* Avoid copying long excerpts from the PDF.
* Do not expose raw chunks unnecessarily.
* Keep the tone supportive, educational, and clear.

Suggested output structure:

```json
{
  "summary": "string",
  "studentMisconception": "string",
  "correctExplanation": "string",
  "studyRecommendation": "string",
  "references": [
    {
      "title": "string",
      "pageNumber": "number|null",
      "sourceUri": "string|null"
    }
  ]
}
```

## Suggested Gemini Prompt Template

Use or adapt this template:

```txt
You are a Physics tutor helping a student understand a multiple-choice question.

Your answer must be written in Brazilian Portuguese.

Use the retrieved context as the main source of information.
Do not invent citations, page numbers, filenames, or facts that are not supported by the retrieved context.
If the retrieved context is insufficient, clearly state that the retrieved material was not enough to fully support the explanation.

Question:
{question}

Alternatives:
{alternatives}

Student answer:
{student_answer}

Correct answer:
{correct_answer}

Topic:
{topic}

Retrieved context:
{retrieved_context}

Generate a pedagogical feedback with the following JSON structure:

{
  "summary": "Briefly say whether the student answer is correct or incorrect.",
  "studentMisconception": "Explain what the student likely misunderstood.",
  "correctExplanation": "Explain the correct reasoning step by step, in simple language.",
  "studyRecommendation": "Tell the student what concept to review and where to study based on the retrieved context.",
  "references": [
    {
      "title": "Document title or filename when available",
      "pageNumber": "Page number when available, otherwise null",
      "sourceUri": "Document URI when available, otherwise null"
    }
  ]
}

Rules:
- Return only valid JSON.
- The content must be in Brazilian Portuguese.
- Be concise but specific.
- Do not copy long excerpts from the source material.
- Do not mention internal implementation details such as RAG, chunks, embeddings, Discovery Engine, or Agent Search to the student.
```

## API Endpoint

Adapt the existing feedback endpoint or create a new one.

Suggested endpoint:

```http
POST /feedback/generate
```

Suggested request:

```json
{
  "questionId": "string",
  "question": "string",
  "alternatives": [
    {
      "label": "A",
      "text": "string"
    }
  ],
  "studentAnswer": "string",
  "correctAnswer": "string",
  "topic": "string"
}
```

Suggested response:

```json
{
  "questionId": "string",
  "feedback": {
    "summary": "string",
    "studentMisconception": "string",
    "correctExplanation": "string",
    "studyRecommendation": "string",
    "references": [
      {
        "title": "string",
        "pageNumber": null,
        "sourceUri": "string"
      }
    ]
  },
  "retrievalMetadata": {
    "query": "string",
    "documentsRetrieved": 5
  }
}
```

If the current project already has a feedback endpoint, adapt the existing endpoint instead of creating a duplicate.

## Error Handling

Implement clear error handling for:

* Missing Google credentials.
* Invalid project/location/engine/data store configuration.
* Discovery Engine search errors.
* Empty retrieval results.
* Gemini generation errors.
* Invalid JSON returned by Gemini.

If no documents are retrieved, the API should still be able to generate a limited feedback, but it must explicitly indicate that no supporting material was found.

## Logging

Add logs for:

* Generated search query.
* Number of documents retrieved.
* Gemini model used.
* Whether the feedback generation succeeded or failed.

Do not log:

* Service account credentials.
* Full private document contents.
* Excessively long prompts.

## Testing

Add or update tests for:

1. Building the search query from question data.
2. Mapping Discovery Engine results into the internal retrieved context format.
3. Building the Gemini prompt.
4. Handling empty retrieval results.
5. Handling invalid Gemini JSON.
6. Returning the expected feedback response structure.

Use mocks for Google Cloud clients. Do not call real Google Cloud services in unit tests.

## Implementation Guidelines

* Follow the existing architecture and dependency injection style of the project.
* Keep retrieval and generation separated.
* Avoid coupling controllers directly to Google Cloud SDKs.
* Keep the code easy to replace in the future.
* Prefer interfaces/ports when appropriate.
* Keep configuration centralized.
* Do not remove the previous implementation unless it is clearly obsolete; if possible, make the provider configurable.

## Optional Provider Strategy

If the project already has a local RAG implementation, introduce a provider strategy:

```txt
RAG_PROVIDER=google_agent_search
```

Possible values:

```txt
local
google_agent_search
```

This allows switching between the previous local RAG pipeline and the new Google-managed RAG pipeline.

## Acceptance Criteria

The task is complete when:

* The API can call Google Agent Search / Discovery Engine to retrieve relevant PDF chunks.
* The API can call Gemini with the retrieved context.
* The feedback is generated in Brazilian Portuguese.
* The response has a structured JSON format.
* The implementation does not manually create embeddings for this Google RAG flow.
* Google Cloud configuration is externalized through environment variables.
* Unit tests cover the main mapping, prompt-building, and error-handling logic.

