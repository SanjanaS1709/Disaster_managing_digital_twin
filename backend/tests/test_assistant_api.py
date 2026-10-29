import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from groq import APITimeoutError, RateLimitError

import app.api.assistant as assistant_api
from app.api.assistant import router
from app.main import app as production_app


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_memory():
    with assistant_api._state_lock:
        assistant_api._sessions.clear()
        assistant_api._requests_by_client.clear()


def configured(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-never-sent-to-client")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")


def test_chat_uses_server_context_and_keeps_short_session_history(monkeypatch):
    configured(monkeypatch)
    calls = []

    async def fake_provider(messages, api_key, model):
        calls.append((messages, api_key, model))
        return f"Mocked answer {len(calls)}"

    monkeypatch.setattr(assistant_api, "_ask_provider", fake_provider)
    http = client()
    first = http.post("/api/assistant/chat", json={"message": "Explain this area's risk."})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["answer"] == "Mocked answer 1"
    assert first_body["session_id"]
    assert first_body["sources"] == []
    assert "simulated" in first_body["safety_notice"].lower()
    first_prompt = "\n".join(message["content"] for message in calls[0][0])
    assert "Simulation/demo data; not a live emergency feed" in first_prompt
    assert "test-key-never-sent-to-client" not in first.text

    second = http.post("/api/assistant/chat", json={"message": "What did I ask?", "session_id": first_body["session_id"]})
    assert second.status_code == 200
    assert second.json()["session_id"] == first_body["session_id"]
    assert any("Explain this area's risk." in message["content"] for message in calls[1][0])


def test_missing_api_key_returns_configuration_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    response = client().post("/api/assistant/chat", json={"message": "How should I prepare?"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "assistant_not_configured"


def test_empty_or_oversized_message_is_rejected(monkeypatch):
    configured(monkeypatch)
    assert client().post("/api/assistant/chat", json={"message": "  "}).status_code == 422
    assert client().post("/api/assistant/chat", json={"message": "x" * 2001}).status_code == 422


def test_provider_timeout_returns_safe_error(monkeypatch):
    configured(monkeypatch)

    async def timeout(*_args, **_kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))

    monkeypatch.setattr(assistant_api, "_ask_provider", timeout)
    response = client().post("/api/assistant/chat", json={"message": "How should I prepare?"})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "provider_timeout"


def test_provider_rate_limit_returns_retry_message(monkeypatch):
    configured(monkeypatch)
    request = httpx.Request("POST", "https://api.groq.com")
    provider_response = httpx.Response(429, request=request)

    async def rate_limited(*_args, **_kwargs):
        raise RateLimitError("rate limited", response=provider_response, body={})

    monkeypatch.setattr(assistant_api, "_ask_provider", rate_limited)
    response = client().post("/api/assistant/chat", json={"message": "How should I prepare?"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "provider_rate_limited"


def test_emergency_notice_and_existing_api_routes_remain_available(monkeypatch):
    assert "112" in assistant_api._safety_notice("I am trapped by floodwater")
    assert "simulated" in assistant_api._safety_notice("Can I drive this flooded road?").lower()
    assert TestClient(production_app).get("/").status_code == 200


def test_frontend_has_loading_error_and_clear_conversation_states():
    root = assistant_api.REPOSITORY_ROOT
    script = (root / "frontend" / "js" / "assistant.js").read_text(encoding="utf-8-sig")
    markup = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "setPending(true)" in script
    assert "assistant-message-error" in script
    assert 'id="assistant-clear"' in markup
    assert 'id="assistant-messages"' in markup
