from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.core.config import get_settings
from app.rag.errors import GoogleRAGConfigurationError, GoogleRAGRetrievalError
from app.rag.providers import RetrievedContextItem

logger = logging.getLogger(__name__)


def _truncate_log_text(value: str, limit: int = 220) -> str:
    text = (value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _maybe_get(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _coerce_page_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            return int(digits)
    return None


def _to_plain_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_plain_value(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_plain_value(v) for k, v in value.items()}

    descriptor = getattr(value, "DESCRIPTOR", None)
    if descriptor is not None:
        try:
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(value, preserving_proto_field_name=True)
        except Exception:
            pass

    items = getattr(value, "items", None)
    if callable(items):
        try:
            return {str(k): _to_plain_value(v) for k, v in items()}
        except Exception:
            return {}

    return value


def _to_plain_dict(value: Any) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): _to_plain_value(v) for k, v in value.items()}
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return {str(k): _to_plain_value(v) for k, v in items()}
        except Exception:
            return {}
    return {}


def _extract_page_from_text(*values: Any) -> int | None:
    patterns = [
        r"\bpage(?:Number)?\D{0,8}(\d{1,5})\b",
        r"\bp[aá]gina\D{0,8}(\d{1,5})\b",
        r"\bpag\.\D{0,4}(\d{1,5})\b",
    ]
    for raw in values:
        if not raw:
            continue
        text = str(raw)
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def _extractive_segments_data(result: Any, derived_dict: dict[str, object]) -> list[dict[str, object]]:
    extractive_segments = _maybe_get(result, "extractive_segments") or derived_dict.get("extractive_segments") or []
    if not extractive_segments:
        return []

    normalized: list[dict[str, object]] = []
    for segment in extractive_segments:
        if isinstance(segment, dict):
            normalized.append({str(k): _to_plain_value(v) for k, v in segment.items()})
        else:
            normalized.append(_to_plain_dict(segment))
    return normalized


def build_agent_search_query(
    *,
    question: str,
    topic: str | None,
    correct_answer: str,
    alternatives: list[str] | None,
    student_answer: str | None = None,
) -> str:
    parts: list[str] = []
    if topic:
        parts.append(f"Topico: {topic}")
    parts.append(f"Questao: {question}")
    parts.append(f"Resposta correta: {correct_answer}")
    if alternatives:
        parts.append("Alternativas relevantes: " + "; ".join(item for item in alternatives if item))
    if student_answer:
        parts.append(f"Resposta do aluno: {student_answer}")
    return "\n".join(part.strip() for part in parts if part.strip())


class GoogleAgentSearchService:
    def __init__(self, client: Any | None = None) -> None:
        self.settings = get_settings()
        self._client = client

    def build_serving_config_path(self) -> str:
        project_id = self.settings.google_cloud_project_id or self.settings.google_cloud_project
        if not project_id:
            raise GoogleRAGConfigurationError("GOOGLE_CLOUD_PROJECT_ID is required for google_agent_search")

        location = self.settings.google_cloud_location
        serving_config = self.settings.google_discovery_serving_config

        if self.settings.google_discovery_engine_id:
            return (
                f"projects/{project_id}/locations/{location}/collections/default_collection/"
                f"engines/{self.settings.google_discovery_engine_id}/servingConfigs/{serving_config}"
            )
        if self.settings.google_discovery_data_store_id:
            return (
                f"projects/{project_id}/locations/{location}/collections/default_collection/"
                f"dataStores/{self.settings.google_discovery_data_store_id}/servingConfigs/{serving_config}"
            )
        raise GoogleRAGConfigurationError(
            "Set GOOGLE_DISCOVERY_ENGINE_ID or GOOGLE_DISCOVERY_DATA_STORE_ID for google_agent_search"
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self.settings.google_application_credentials:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS",
                self.settings.google_application_credentials,
            )
        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
        except ImportError as exc:  # pragma: no cover
            raise GoogleRAGConfigurationError(
                "google-cloud-discoveryengine is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        self._client = discoveryengine.SearchServiceClient()
        return self._client

    def search_relevant_context(self, query: str, page_size: int | None = None) -> list[RetrievedContextItem]:
        client = self._get_client()
        serving_config = self.build_serving_config_path()
        page_size = page_size or self.settings.google_agent_search_page_size
        logger.info(
            "Agent Search query=%r provider=google_agent_search page_size=%d",
            query[:500],
            page_size,
        )
        logger.info("Agent Search serving config: %s", serving_config)

        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
        except ImportError as exc:  # pragma: no cover
            raise GoogleRAGConfigurationError(
                "google-cloud-discoveryengine is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=page_size,
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_segment_count=page_size,
                    return_extractive_segment_score=True,
                ),
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                )
            ),
        )

        try:
            response = client.search(request=request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Discovery Engine search failed")
            raise GoogleRAGRetrievalError("Discovery Engine search failed") from exc

        first_result = next(iter(response.results), None)
        if first_result is not None:
            logger.info("Discovery Engine first result shape: %s", first_result)
        else:
            logger.info("Discovery Engine returned no results for query=%r", query[:500])

        results: list[RetrievedContextItem] = []
        for rank, result in enumerate(response.results, start=1):
            normalized_items = self._normalize_result(result, rank)
            results.extend(normalized_items)
            if len(results) >= page_size:
                results = results[:page_size]
                break
        logger.info("Agent Search retrieved %d context items", len(results))
        for item in results:
            logger.info(
                "Agent Search normalized result rank=%s id=%s title=%r page=%s source_uri=%r snippet=%r",
                item.rank,
                item.id,
                item.title,
                item.page_number,
                item.source_uri,
                _truncate_log_text(item.snippet),
            )
        return results

    def _normalize_result(self, result: Any, rank: int) -> list[RetrievedContextItem]:
        document = _maybe_get(result, "document") or {}
        chunk = _maybe_get(result, "chunk") or {}
        derived = _maybe_get(document, "derived_struct_data") or {}
        struct = _maybe_get(document, "struct_data") or {}
        derived_dict = _to_plain_dict(derived)
        struct_dict = _to_plain_dict(struct)
        chunk_dict = _to_plain_dict(chunk)
        extractive_segments = _extractive_segments_data(result, derived_dict)
        if extractive_segments:
            return [
                self._normalize_result_item(
                    result=result,
                    rank=rank,
                    derived_dict=derived_dict,
                    struct_dict=struct_dict,
                    chunk_dict=chunk_dict,
                    extractive_segment=segment,
                    segment_index=segment_index,
                )
                for segment_index, segment in enumerate(extractive_segments, start=1)
            ]
        return [
            self._normalize_result_item(
                result=result,
                rank=rank,
                derived_dict=derived_dict,
                struct_dict=struct_dict,
                chunk_dict=chunk_dict,
                extractive_segment={},
                segment_index=1,
            )
        ]

    def _normalize_result_item(
        self,
        *,
        result: Any,
        rank: int,
        derived_dict: dict[str, object],
        struct_dict: dict[str, object],
        chunk_dict: dict[str, object],
        extractive_segment: dict[str, object],
        segment_index: int,
    ) -> RetrievedContextItem:
        document = _maybe_get(result, "document") or {}
        chunk = _maybe_get(result, "chunk") or {}
        extractive_page_span = (
            extractive_segment.get("page_span")
            or extractive_segment.get("pageSpan")
            or {}
        )
        extractive_page_span_dict = _to_plain_dict(extractive_page_span)

        page_span = _maybe_get(chunk, "page_span", "pageSpan") or chunk_dict.get("page_span") or chunk_dict.get("pageSpan") or {}
        page_span_dict = _to_plain_dict(page_span)

        base_identifier = _maybe_get(document, "id", "name") or _maybe_get(result, "id") or f"result-{rank}"
        segment_identifier = extractive_segment.get("id") or f"s{segment_index}"
        identifier = f"{base_identifier}:{segment_identifier}" if extractive_segment else str(base_identifier)
        title = (
            _maybe_get(_maybe_get(chunk, "document_metadata", "documentMetadata") or {}, "title")
            or _maybe_get(document, "title")
            or derived_dict.get("title")
            or struct_dict.get("title")
            or struct_dict.get("filename")
            or derived_dict.get("filename")
        )
        source_uri = (
            _maybe_get(_maybe_get(chunk, "document_metadata", "documentMetadata") or {}, "uri")
            or _maybe_get(document, "uri")
            or _maybe_get(document, "link")
            or derived_dict.get("link")
            or derived_dict.get("uri")
            or struct_dict.get("uri")
            or struct_dict.get("source")
            or struct_dict.get("gcs_uri")
        )
        page_number = _coerce_page_number(
            _maybe_get(page_span, "page_start", "pageStart")
            or page_span_dict.get("page_start")
            or page_span_dict.get("pageStart")
            or extractive_page_span_dict.get("page_start")
            or extractive_page_span_dict.get("pageStart")
            or extractive_page_span_dict.get("page_end")
            or extractive_page_span_dict.get("pageEnd")
            or extractive_segment.get("pageNumber")
            or extractive_segment.get("page_number")
            or derived_dict.get("pageNumber")
            or derived_dict.get("page_number")
            or struct_dict.get("pageNumber")
            or struct_dict.get("page_number")
            or struct_dict.get("page")
        )

        snippets = _maybe_get(result, "snippets") or []
        snippet_text = ""
        if snippets:
            first = snippets[0]
            snippet_text = (
                _maybe_get(first, "snippet")
                or _maybe_get(first, "snippet_text")
                or _maybe_get(first, "text")
                or ""
            )
        if not snippet_text:
            snippet_text = str(extractive_segment.get("content") or "")
        if not snippet_text:
            snippet_text = str(_maybe_get(chunk, "content") or chunk_dict.get("content") or "")
        if not snippet_text:
            snippet_text = str(
                derived_dict.get("snippets")
                or derived_dict.get("content")
                or struct_dict.get("content")
                or ""
            )

        if page_number is None:
            page_number = _extract_page_from_text(
                extractive_segment.get("content"),
                snippet_text,
                derived_dict.get("content"),
                struct_dict.get("content"),
            )

        if page_number is None:
            logger.warning(
                "Agent Search result without page metadata rank=%s id=%s title=%r chunk=%r extractive_segment=%r",
                rank,
                identifier,
                title,
                chunk_dict,
                extractive_segment,
            )

        metadata = {
            "document_name": _maybe_get(document, "name"),
            "struct_data": struct_dict,
            "derived_struct_data": derived_dict,
            "chunk": chunk_dict,
            "extractive_segment": extractive_segment,
        }
        return RetrievedContextItem(
            id=str(identifier),
            title=str(title) if title else None,
            source_uri=str(source_uri) if source_uri else None,
            page_number=page_number,
            snippet=snippet_text.strip(),
            metadata=metadata,
            rank=rank,
        )
