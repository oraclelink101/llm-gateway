"""API tests: end-to-end through FastAPI (router + scheduler + backend)."""

from fastapi.testclient import TestClient

from llm_gateway.app import app


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_chat_simple_routes_small():
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            json={"prompt": "What is 2 + 2?", "priority": "normal", "max_tokens": 16},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["content"]
        assert body["metadata"]["routed_model"] == "small"
        assert body["metadata"]["cost_usd"] > 0
        assert body["metadata"]["total_ms"] >= 0


def test_chat_complex_routes_large():
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            json={
                "prompt": (
                    "Analyze and design a fault-tolerant architecture, compare "
                    "trade-offs, and explain the algorithm step by step."
                ),
                "priority": "high",
                "max_tokens": 64,
            },
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["routed_model"] == "large"


def test_chat_validation_error():
    with TestClient(app) as client:
        r = client.post("/v1/chat", json={"prompt": "", "max_tokens": 16})
        assert r.status_code == 422  # empty prompt rejected by pydantic
