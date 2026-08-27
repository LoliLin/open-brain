from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from open_brain.app import app, store


def setup_function() -> None:
    store._jobs.clear()
    store._listeners.clear()


def _complete_next(content: str) -> None:
    def worker() -> None:
        for _ in range(100):
            pending = store.pending()
            if pending:
                store.complete(pending[0], content)
                return
            time.sleep(0.02)
        raise AssertionError("no pending job")

    threading.Thread(target=worker, daemon=True).start()


def test_models_and_chat_roundtrip() -> None:
    with TestClient(app) as client:
        models = client.get("/v1/models")
        assert models.status_code == 200
        ids = [m["id"] for m in models.json()["data"]]
        assert "human" in ids

        _complete_next("你好，我是人工模型。")
        res = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"] == "你好，我是人工模型。"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert "usage" in body


def test_stream_chat() -> None:
    with TestClient(app) as client:
        _complete_next("ABC")
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "human",
                "stream": True,
                "messages": [{"role": "user", "content": "stream please"}],
            },
        ) as res:
            assert res.status_code == 200
            text = "".join(res.iter_text())
        assert "data: " in text
        assert "[DONE]" in text
        contents = []
        for line in text.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            payload = json.loads(line[6:])
            delta = payload["choices"][0].get("delta") or {}
            if delta.get("content"):
                contents.append(delta["content"])
        assert "".join(contents) == "ABC"


def test_operator_http_reply() -> None:
    with TestClient(app) as client:
        created = store.create(
            kind="chat",
            model="human",
            stream=False,
            payload={},
            messages=[{"role": "user", "content": "ping"}],
        )
        listed = client.get("/operator/jobs")
        assert listed.status_code == 200
        assert any(j["id"] == created.id for j in listed.json()["jobs"])

        res = client.post(
            f"/operator/jobs/{created.id}/reply",
            json={"content": "pong"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "completed"
        assert created.event.is_set()


def test_reject() -> None:
    with TestClient(app) as client:
        def worker() -> None:
            for _ in range(100):
                pending = store.pending()
                if pending:
                    store.reject(pending[0], "nope")
                    return
                time.sleep(0.02)

        threading.Thread(target=worker, daemon=True).start()
        res = client.post("/v1/completions", json={"model": "human", "prompt": "say hi"})
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "operator_rejected"
