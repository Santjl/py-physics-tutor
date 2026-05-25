from __future__ import annotations

import logging
import random
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterable

from app.core.config import get_settings

logger = logging.getLogger(__name__)
DEFAULT_EMBED_DIM = 768

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 10.0
EMBED_BATCH_SIZE = 100
MAX_PROMPT_CHARS = 900_000
QUOTA_COOLDOWN_SECONDS = 30.0

_quota_lock = threading.Lock()
_quota_cooldown_until = 0.0


class QuotaExceededError(RuntimeError):
    """Raised when the provider returns a quota/rate-limit error."""


def _stringify_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
                    continue
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(part for part in parts if part)
    return str(content)


def _message_role(message: Any) -> str:
    role = getattr(message, "type", None)
    if isinstance(role, str) and role:
        return role.lower()
    return message.__class__.__name__.lower()


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    candidates = getattr(response, "candidates", None) or []
    parts: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        content_parts = getattr(content, "parts", None) or []
        for part in content_parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                parts.append(part_text)

    if parts:
        return "\n".join(parts)

    raise RuntimeError("Gemini returned an empty response")


class _GoogleGenAIClientBase:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "google-genai is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        if self.settings.google_genai_api_key:
            self._client = genai.Client(api_key=self.settings.google_genai_api_key)
            return self._client

        if not self.settings.google_cloud_project:
            raise RuntimeError(
                "Set GOOGLE_CLOUD_PROJECT for Vertex AI auth or GOOGLE_GENAI_API_KEY for Gemini API auth."
            )

        self._client = genai.Client(
            vertexai=True,
            project=self.settings.google_cloud_project,
            location=self.settings.google_cloud_location,
        )
        return self._client

    @staticmethod
    def _is_quota_error(exc: Exception) -> bool:
        message = str(exc).upper()
        return "429" in message or "RESOURCE_EXHAUSTED" in message or "TOO MANY REQUESTS" in message

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Treat quota/rate limits as retryable and fail fast on permanent 4xx."""
        if _GoogleGenAIClientBase._is_quota_error(exc):
            return True

        try:
            from google.genai.errors import APIError, ClientError
        except ImportError:
            return True

        if isinstance(exc, APIError):
            code = getattr(exc, "code", None)
            if code == 429:
                return True
        if isinstance(exc, ClientError):
            return False
        return True

    @staticmethod
    def _next_backoff(current: float) -> float:
        # Jitter reduces thundering-herd retries when many requests fail together.
        jitter = random.uniform(0.0, current * 0.25)
        return min(current * 2 + jitter, MAX_BACKOFF)

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except (TypeError, ValueError):
                    pass

        match = re.search(r"RETRY[- ]AFTER[^0-9]*([0-9]+(?:\.[0-9]+)?)", str(exc), flags=re.IGNORECASE)
        if match:
            return max(0.0, float(match.group(1)))
        return None

    def _wait_for_quota_cooldown(self) -> None:
        while True:
            with _quota_lock:
                remaining = _quota_cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            logger.warning("Quota cooldown active for %.1fs; delaying Gemini request", remaining)
            time.sleep(min(remaining, 1.0))

    def _activate_quota_cooldown(self, exc: Exception, fallback_seconds: float) -> float:
        cooldown = self._retry_after_seconds(exc) or fallback_seconds or QUOTA_COOLDOWN_SECONDS
        deadline = time.monotonic() + cooldown
        with _quota_lock:
            global _quota_cooldown_until
            _quota_cooldown_until = max(_quota_cooldown_until, deadline)
        return cooldown


class GoogleGenAIEmbeddingClient(_GoogleGenAIClientBase):
    def __init__(self) -> None:
        super().__init__()
        self.embed_model = self.settings.google_embed_model

    def _embed_once(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        response = client.models.embed_content(model=self.embed_model, contents=texts)

        embeddings = getattr(response, "embeddings", None)
        if embeddings is None:
            single = getattr(response, "embedding", None)
            embeddings = [single] if single is not None else []

        vectors: list[list[float]] = []
        for item in embeddings:
            values = getattr(item, "values", None)
            if values is None and isinstance(item, dict):
                values = item.get("values") or item.get("embedding")
            if values is None:
                vectors.append([])
            else:
                vectors.append(list(values))
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            logger.warning("Lista de textos vazia para embedding")
            return []

        if self.settings.app_env.lower() == "test":
            return [[0.1] * DEFAULT_EMBED_DIM for _ in texts]

        # Batch into safe-sized chunks to avoid oversized single requests
        if len(texts) > EMBED_BATCH_SIZE:
            all_vectors: list[list[float]] = []
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                batch = texts[i : i + EMBED_BATCH_SIZE]
                all_vectors.extend(self.embed(batch))
            return all_vectors

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._wait_for_quota_cooldown()
                embeddings = self._embed_once(texts)
                if len(embeddings) != len(texts):
                    raise RuntimeError(
                        f"Unexpected embedding response size: got {len(embeddings)}, expected {len(texts)}"
                    )
                return [self._normalize_vector(vec, idx, len(texts)) for idx, vec in enumerate(embeddings, start=1)]
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not self._is_retryable(exc):
                    logger.error("Non-retryable error during embedding: %s", exc)
                    break
                if self._is_quota_error(exc):
                    backoff = max(backoff, self._activate_quota_cooldown(exc, QUOTA_COOLDOWN_SECONDS))
                logger.warning(
                    "Embedding attempt %d/%d failed, retrying in %.1fs",
                    attempt,
                    MAX_RETRIES,
                    backoff,
                    exc_info=True,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff = self._next_backoff(backoff)

        if last_error and self._is_quota_error(last_error):
            raise QuotaExceededError("Google embedding quota exhausted") from last_error
        raise RuntimeError("Failed to embed text") from last_error

    def _normalize_vector(self, vec: Iterable[float] | None, idx: int, total: int) -> list[float]:
        if not vec:
            logger.warning("Empty embedding vector for text %d/%d; using zero vector", idx, total)
            return [0.0] * DEFAULT_EMBED_DIM

        values = list(vec)
        if len(values) != DEFAULT_EMBED_DIM:
            logger.warning(
                "Unexpected embedding dim for text %d/%d: got %d, expected %d",
                idx,
                total,
                len(values),
                DEFAULT_EMBED_DIM,
            )
            values = (values + [0.0] * DEFAULT_EMBED_DIM)[:DEFAULT_EMBED_DIM]
        return values


class GoogleGenAIChatClient(_GoogleGenAIClientBase):
    def __init__(self, temperature: float = 0.0) -> None:
        super().__init__()
        self.model = self.settings.google_chat_model
        self.temperature = temperature

    def invoke(self, messages: list[Any]) -> SimpleNamespace:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            text = _stringify_message_content(getattr(message, "content", ""))
            if not text.strip():
                continue
            if "system" in _message_role(message):
                system_parts.append(text)
            else:
                user_parts.append(text)

        prompt_parts: list[str] = []
        if system_parts:
            prompt_parts.append("System instructions:\n" + "\n\n".join(system_parts))
        if user_parts:
            prompt_parts.append("User request:\n" + "\n\n".join(user_parts))
        prompt = "\n\n".join(prompt_parts).strip()
        if not prompt:
            raise RuntimeError("Cannot invoke Gemini with an empty prompt")

        if len(prompt) > MAX_PROMPT_CHARS:
            logger.warning(
                "Prompt too large (%d chars, limit %d); truncating to avoid excessive billing",
                len(prompt),
                MAX_PROMPT_CHARS,
            )
            prompt = prompt[:MAX_PROMPT_CHARS]

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._wait_for_quota_cooldown()
                client = self._get_client()
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={"temperature": self.temperature},
                )
                return SimpleNamespace(content=_extract_response_text(response))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not self._is_retryable(exc):
                    logger.error("Non-retryable error during generation: %s", exc)
                    break
                if self._is_quota_error(exc):
                    backoff = max(backoff, self._activate_quota_cooldown(exc, QUOTA_COOLDOWN_SECONDS))
                logger.warning(
                    "Gemini attempt %d/%d failed, retrying in %.1fs",
                    attempt,
                    MAX_RETRIES,
                    backoff,
                    exc_info=True,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff = self._next_backoff(backoff)

        if last_error and self._is_quota_error(last_error):
            raise QuotaExceededError("Google generation quota exhausted") from last_error
        raise RuntimeError("Failed to generate content with Gemini") from last_error
