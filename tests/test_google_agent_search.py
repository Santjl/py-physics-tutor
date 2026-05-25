import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core.config import get_settings
from app.rag.google_agent_search import GoogleAgentSearchService, build_agent_search_query


def test_build_agent_search_query_includes_core_fields():
    query = build_agent_search_query(
        question="Qual e a relacao entre forca e aceleracao?",
        topic="Leis de Newton",
        correct_answer="Aceleracao proporcional a forca resultante.",
        alternatives=["Inercia", "Aceleracao", "Calor"],
        student_answer="Inercia",
    )
    assert "Leis de Newton" in query
    assert "Qual e a relacao" in query
    assert "Aceleracao proporcional" in query
    assert "Inercia" in query


def test_build_serving_config_path_prefers_data_store(monkeypatch):
    settings = get_settings()
    original = {
        "google_cloud_project_id": settings.google_cloud_project_id,
        "google_cloud_project": settings.google_cloud_project,
        "google_cloud_location": settings.google_cloud_location,
        "google_discovery_engine_id": settings.google_discovery_engine_id,
        "google_discovery_data_store_id": settings.google_discovery_data_store_id,
        "google_discovery_serving_config": settings.google_discovery_serving_config,
    }
    settings.google_cloud_project_id = "proj-1"
    settings.google_cloud_project = None
    settings.google_cloud_location = "global"
    settings.google_discovery_engine_id = None
    settings.google_discovery_data_store_id = "store-1"
    settings.google_discovery_serving_config = "default_search"
    try:
        service = GoogleAgentSearchService(client=object())
        path = service.build_serving_config_path()
    finally:
        for key, value in original.items():
            setattr(settings, key, value)

    assert "/dataStores/store-1/" in path
    assert path.endswith("/servingConfigs/default_search")


def test_normalize_result_maps_fields():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={
                "title": "fisica.pdf",
                "link": "gs://bucket/fisica.pdf",
                "pageNumber": 7,
            },
            struct_data={"content": "Trecho estrutural"},
        ),
        snippets=[SimpleNamespace(snippet="Trecho relevante")],
    )
    item = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)[0]
    assert item.id == "doc-1"
    assert item.title == "fisica.pdf"
    assert item.source_uri == "gs://bucket/fisica.pdf"
    assert item.page_number == 7
    assert item.snippet == "Trecho relevante"


def test_normalize_result_prefers_chunk_page_span():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={"title": "fisica.pdf"},
            struct_data={},
        ),
        chunk=SimpleNamespace(
            content="Texto do chunk",
            page_span=SimpleNamespace(page_start=12, page_end=12),
            document_metadata=SimpleNamespace(
                title="fisica.pdf",
                uri="gs://bucket/fisica.pdf",
            ),
        ),
        snippets=[],
    )

    item = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)[0]

    assert item.page_number == 12
    assert item.title == "fisica.pdf"
    assert item.source_uri == "gs://bucket/fisica.pdf"
    assert item.snippet == "Texto do chunk"


def test_normalize_result_uses_extractive_segment_page_number():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={
                "title": "fisica.pdf",
                "extractive_segments": [
                    {
                        "content": "Trecho extraido",
                        "pageNumber": 9,
                        "relevanceScore": 0.91,
                    }
                ],
            },
            struct_data={},
        ),
        snippets=[],
    )

    item = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)[0]

    assert item.page_number == 9
    assert item.snippet == "Trecho extraido"
    assert item.metadata["extractive_segment"]["pageNumber"] == 9


def test_normalize_result_uses_extractive_segment_page_span():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={
                "title": "fisica.pdf",
                "extractive_segments": [
                    {
                        "content": "Trecho extraido",
                        "page_span": {
                            "page_start": 17,
                            "page_end": 20,
                        },
                    }
                ],
            },
            struct_data={},
        ),
        snippets=[],
    )

    item = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)[0]

    assert item.page_number == 17
    assert item.snippet == "Trecho extraido"


def test_normalize_result_extracts_page_from_text_fallback():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={
                "title": "fisica.pdf",
                "extractive_segments": [
                    {
                        "content": "Pagina 14: trecho importante sobre MRU.",
                    }
                ],
            },
            struct_data={},
        ),
        snippets=[],
    )

    item = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)[0]

    assert item.page_number == 14
    assert item.snippet == "Pagina 14: trecho importante sobre MRU."


def test_normalize_result_expands_multiple_extractive_segments():
    result = SimpleNamespace(
        document=SimpleNamespace(
            id="doc-1",
            derived_struct_data={
                "title": "fisica.pdf",
                "link": "gs://bucket/fisica.pdf",
                "extractive_segments": [
                    {
                        "id": "c9",
                        "content": "Trecho sobre forca resultante",
                        "page_span": {"page_start": 17, "page_end": 20},
                    },
                    {
                        "id": "c46",
                        "content": "Trecho sobre centro de massa",
                        "page_span": {"page_start": 78, "page_end": 85},
                    },
                ],
            },
            struct_data={},
        ),
        snippets=[],
    )

    items = GoogleAgentSearchService(client=object())._normalize_result(result, rank=1)

    assert len(items) == 2
    assert items[0].id == "doc-1:c9"
    assert items[0].page_number == 17
    assert items[0].snippet == "Trecho sobre forca resultante"
    assert items[1].id == "doc-1:c46"
    assert items[1].page_number == 78
    assert items[1].snippet == "Trecho sobre centro de massa"


def test_build_serving_config_path_requires_resource(monkeypatch):
    settings = get_settings()
    original_project = settings.google_cloud_project_id
    original_store = settings.google_discovery_data_store_id
    original_engine = settings.google_discovery_engine_id
    settings.google_cloud_project_id = "proj-1"
    settings.google_discovery_data_store_id = None
    settings.google_discovery_engine_id = None
    try:
        service = GoogleAgentSearchService(client=object())
        with pytest.raises(RuntimeError):
            service.build_serving_config_path()
    finally:
        settings.google_cloud_project_id = original_project
        settings.google_discovery_data_store_id = original_store
        settings.google_discovery_engine_id = original_engine


def test_search_relevant_context_logs_normalized_results(caplog, monkeypatch):
    discoveryengine_module = ModuleType("discoveryengine_v1")

    class FakeSearchRequest:
        class ContentSearchSpec:
            class ExtractiveContentSpec:
                def __init__(
                    self,
                    max_extractive_answer_count=None,
                    max_extractive_segment_count=None,
                    return_extractive_segment_score=None,
                ):
                    self.max_extractive_answer_count = max_extractive_answer_count
                    self.max_extractive_segment_count = max_extractive_segment_count
                    self.return_extractive_segment_score = return_extractive_segment_score

            class SnippetSpec:
                def __init__(self, return_snippet):
                    self.return_snippet = return_snippet

            def __init__(self, snippet_spec=None, extractive_content_spec=None):
                self.snippet_spec = snippet_spec
                self.extractive_content_spec = extractive_content_spec

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    discoveryengine_module.SearchRequest = FakeSearchRequest
    google_cloud_module = ModuleType("google.cloud")
    google_cloud_module.discoveryengine_v1 = discoveryengine_module
    monkeypatch.setitem(sys.modules, "google.cloud", google_cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.discoveryengine_v1", discoveryengine_module)

    service = GoogleAgentSearchService(
        client=SimpleNamespace(
            search=lambda request: SimpleNamespace(
                results=[
                    SimpleNamespace(
                        document=SimpleNamespace(
                            id="doc-1",
                            derived_struct_data={
                                "title": "fisica.pdf",
                                "link": "gs://bucket/fisica.pdf",
                                "pageNumber": 7,
                            },
                            struct_data={},
                        ),
                        snippets=[SimpleNamespace(snippet="Trecho relevante de teste")],
                    )
                ]
            )
        )
    )

    original_settings = {
        "google_cloud_project_id": service.settings.google_cloud_project_id,
        "google_cloud_project": service.settings.google_cloud_project,
        "google_cloud_location": service.settings.google_cloud_location,
        "google_discovery_engine_id": service.settings.google_discovery_engine_id,
        "google_discovery_data_store_id": service.settings.google_discovery_data_store_id,
        "google_discovery_serving_config": service.settings.google_discovery_serving_config,
        "google_agent_search_page_size": service.settings.google_agent_search_page_size,
    }
    service.settings.google_cloud_project_id = "proj-1"
    service.settings.google_cloud_project = None
    service.settings.google_cloud_location = "global"
    service.settings.google_discovery_engine_id = None
    service.settings.google_discovery_data_store_id = "store-1"
    service.settings.google_discovery_serving_config = "default_search"
    service.settings.google_agent_search_page_size = 4

    try:
        caplog.set_level("INFO")
        items = service.search_relevant_context("teste")
    finally:
        for key, value in original_settings.items():
            setattr(service.settings, key, value)

    assert len(items) == 1
    assert "Agent Search normalized result" in caplog.text
    assert "fisica.pdf" in caplog.text
    assert "Trecho relevante de teste" in caplog.text


def test_search_relevant_context_requests_extractive_segments(monkeypatch):
    captured = {}
    discoveryengine_module = ModuleType("discoveryengine_v1")

    class FakeSearchRequest:
        class ContentSearchSpec:
            class ExtractiveContentSpec:
                def __init__(
                    self,
                    max_extractive_answer_count=None,
                    max_extractive_segment_count=None,
                    return_extractive_segment_score=None,
                ):
                    self.max_extractive_answer_count = max_extractive_answer_count
                    self.max_extractive_segment_count = max_extractive_segment_count
                    self.return_extractive_segment_score = return_extractive_segment_score

            class SnippetSpec:
                def __init__(self, return_snippet):
                    self.return_snippet = return_snippet

            def __init__(self, snippet_spec=None, extractive_content_spec=None):
                self.snippet_spec = snippet_spec
                self.extractive_content_spec = extractive_content_spec

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    discoveryengine_module.SearchRequest = FakeSearchRequest
    google_cloud_module = ModuleType("google.cloud")
    google_cloud_module.discoveryengine_v1 = discoveryengine_module
    monkeypatch.setitem(sys.modules, "google.cloud", google_cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.discoveryengine_v1", discoveryengine_module)

    def fake_search(request):
        captured["request"] = request
        return SimpleNamespace(results=[])

    service = GoogleAgentSearchService(client=SimpleNamespace(search=fake_search))
    original_settings = {
        "google_cloud_project_id": service.settings.google_cloud_project_id,
        "google_cloud_project": service.settings.google_cloud_project,
        "google_cloud_location": service.settings.google_cloud_location,
        "google_discovery_engine_id": service.settings.google_discovery_engine_id,
        "google_discovery_data_store_id": service.settings.google_discovery_data_store_id,
        "google_discovery_serving_config": service.settings.google_discovery_serving_config,
        "google_agent_search_page_size": service.settings.google_agent_search_page_size,
    }
    service.settings.google_cloud_project_id = "proj-1"
    service.settings.google_cloud_project = None
    service.settings.google_cloud_location = "global"
    service.settings.google_discovery_engine_id = None
    service.settings.google_discovery_data_store_id = "store-1"
    service.settings.google_discovery_serving_config = "default_search"
    service.settings.google_agent_search_page_size = 4

    try:
        service.search_relevant_context("teste")
    finally:
        for key, value in original_settings.items():
            setattr(service.settings, key, value)

    request = captured["request"]
    content_search_spec = request.kwargs["content_search_spec"]
    assert content_search_spec.snippet_spec.return_snippet is True
    assert content_search_spec.extractive_content_spec.max_extractive_segment_count == 4
    assert content_search_spec.extractive_content_spec.max_extractive_answer_count is None
    assert content_search_spec.extractive_content_spec.return_extractive_segment_score is True
