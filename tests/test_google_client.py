from types import SimpleNamespace

import pytest

from app.rag import google_client as gc


def test_chat_client_raises_quota_exceeded_for_429(monkeypatch):
    client = gc.GoogleGenAIChatClient()

    monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED")))
    ))
    monkeypatch.setattr(gc.time, "sleep", lambda _: None)
    monkeypatch.setattr(gc, "MAX_RETRIES", 1)

    with pytest.raises(gc.QuotaExceededError):
        client.invoke([SimpleNamespace(type="system", content="sys"), SimpleNamespace(type="human", content="user")])
