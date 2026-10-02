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


def test_tool_calls_reply() -> None:
    with TestClient(app) as client:
        def worker() -> None:
            for _ in range(100):
                pending = store.pending()
                if pending:
                    job = pending[0]
                    tool_calls = [
                        {
                            "id": "call_weather_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": json.dumps({"location": "Beijing"}),
                            },
                        }
                    ]
                    store.complete(job, content=None, tool_calls=tool_calls)
                    return
                time.sleep(0.02)
            raise AssertionError("no pending job")

        threading.Thread(target=worker, daemon=True).start()
        res = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "What's the weather in Beijing?"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "parameters": {"type": "object", "properties": {"location": {"type": "string"}}},
                        },
                    }
                ],
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["choices"][0]["finish_reason"] == "tool_calls"
        message = body["choices"][0]["message"]
        assert message["role"] == "assistant"
        assert len(message["tool_calls"]) == 1
        assert message["tool_calls"][0]["function"]["name"] == "get_weather"
        assert "Beijing" in message["tool_calls"][0]["function"]["arguments"]


def test_tool_calls_stream() -> None:
    with TestClient(app) as client:
        def worker() -> None:
            for _ in range(100):
                pending = store.pending()
                if pending:
                    job = pending[0]
                    tool_calls = [
                        {
                            "id": "call_calc_99",
                            "type": "function",
                            "function": {
                                "name": "calculator",
                                "arguments": json.dumps({"expression": "2+2"}),
                            },
                        }
                    ]
                    store.complete(job, content="Calculating...", tool_calls=tool_calls)
                    return
                time.sleep(0.02)
            raise AssertionError("no pending job")

        threading.Thread(target=worker, daemon=True).start()
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "human",
                "stream": True,
                "messages": [{"role": "user", "content": "calc 2+2"}],
            },
        ) as res:
            assert res.status_code == 200
            text = "".join(res.iter_text())

        assert "data: " in text
        assert "[DONE]" in text
        found_tool_call = False
        for line in text.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            payload = json.loads(line[6:])
            choice = payload["choices"][0]
            delta = choice.get("delta") or {}
            if "tool_calls" in delta:
                found_tool_call = True
        assert found_tool_call


def test_prefix_matching_and_exact_cache() -> None:
    with TestClient(app) as client:
        msg1 = {"role": "user", "content": "Hello world"}
        job1 = store.create(
            kind="chat",
            model="human",
            stream=False,
            payload={},
            messages=[msg1],
        )
        store.complete(job1, content="Hi human!")

        # 1. Exact match test: identical message
        job_exact = store.create(
            kind="chat",
            model="human",
            stream=False,
            payload={},
            messages=[msg1],
        )
        assert job_exact.exact_match is not None
        assert job_exact.exact_match["reply"] == "Hi human!"
        assert job_exact.exact_match["matched_job_id"] == job1.id

        # 2. Multi-turn prefix match test: [msg1, assistant_reply, msg2]
        msg_assistant = {"role": "assistant", "content": "Hi human!"}
        msg2 = {"role": "user", "content": "What can you do?"}
        job_multi_1 = store.create(
            kind="chat",
            model="human",
            stream=False,
            payload={},
            messages=[msg1, msg_assistant],
        )
        store.complete(job_multi_1, content="Awaiting user prompt")

        job_multi_2 = store.create(
            kind="chat",
            model="human",
            stream=False,
            payload={},
            messages=[msg1, msg_assistant, msg2],
        )
        assert job_multi_2.prefix_match is not None
        assert job_multi_2.prefix_match["prefix_count"] == 2
        assert job_multi_2.prefix_match["matched_job_id"] == job_multi_1.id


def test_snippets_and_cache_apis() -> None:
    with TestClient(app) as client:
        res = client.get("/operator/snippets")
        assert res.status_code == 200
        snippets = res.json()
        assert len(snippets) > 0

        # Add snippet
        create_res = client.post(
            "/operator/snippets",
            json={"title": "测试模板", "content": "测试内容"},
        )
        assert create_res.status_code == 200
        created_snip = create_res.json()
        assert created_snip["title"] == "测试模板"

        # Delete snippet
        del_res = client.delete(f"/operator/snippets/{created_snip['id']}")
        assert del_res.status_code == 200

        # Cache stats & clear
        cache_res = client.get("/operator/cache")
        assert cache_res.status_code == 200
        assert "exact_cache_size" in cache_res.json()

        clear_res = client.post("/operator/cache/clear")
        assert clear_res.status_code == 200
        assert client.get("/operator/cache").json()["exact_cache_size"] == 0
