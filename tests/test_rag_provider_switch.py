from app.core.config import get_settings
from app.rag import feedback as fb


def test_generate_feedback_uses_local_provider(monkeypatch):
    settings = get_settings()
    original = settings.rag_provider
    settings.rag_provider = "local"
    called = {"local": False, "google": False}

    def _local(db, attempt):
        called["local"] = True
        return "local-result"

    def _google(db, attempt):
        called["google"] = True
        return "google-result"

    monkeypatch.setattr(fb, "generate_feedback_local", _local)
    monkeypatch.setattr(fb, "generate_feedback_google", _google)
    try:
        result = fb.generate_feedback(None, None)
    finally:
        settings.rag_provider = original

    assert result == "local-result"
    assert called["local"] is True
    assert called["google"] is False


def test_generate_feedback_uses_google_provider(monkeypatch):
    settings = get_settings()
    original = settings.rag_provider
    settings.rag_provider = "google_agent_search"
    called = {"local": False, "google": False}

    def _local(db, attempt):
        called["local"] = True
        return "local-result"

    def _google(db, attempt):
        called["google"] = True
        return "google-result"

    monkeypatch.setattr(fb, "generate_feedback_local", _local)
    monkeypatch.setattr(fb, "generate_feedback_google", _google)
    try:
        result = fb.generate_feedback(None, None)
    finally:
        settings.rag_provider = original

    assert result == "google-result"
    assert called["google"] is True
    assert called["local"] is False
