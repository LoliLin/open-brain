from __future__ import annotations

import json
from typing import Any, Iterator

from open_brain.store import Job


def message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "image_url":
                    url = item.get("image_url")
                    if isinstance(url, dict):
                        parts.append(f"[image:{url.get('url', '')}]")
                    else:
                        parts.append(f"[image:{url}]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(p for p in parts if p)
    return json.dumps(content, ensure_ascii=False)


def prompt_to_messages(prompt: Any) -> list[dict[str, Any]]:
    if isinstance(prompt, list):
        texts = [message_text(p) for p in prompt]
        return [{"role": "user", "content": "\n".join(texts)}]
    return [{"role": "user", "content": message_text(prompt)}]


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    text = "\n".join(f"{m.get('role', '')}: {message_text(m.get('content'))}" for m in messages)
    return max(1, len(text) // 4)


def chat_completion(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "object": "chat.completion",
        "created": job.created,
        "model": job.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": job.reply or ""},
                "finish_reason": job.finish_reason,
                "logprobs": None,
            }
        ],
        "usage": job.usage,
        "system_fingerprint": "open-brain-human",
    }


def text_completion(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "object": "text_completion",
        "created": job.created,
        "model": job.model,
        "choices": [
            {
                "index": 0,
                "text": job.reply or "",
                "finish_reason": job.finish_reason,
                "logprobs": None,
            }
        ],
        "usage": job.usage,
        "system_fingerprint": "open-brain-human",
    }


def openai_error(message: str, *, type_: str = "invalid_request_error", code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


def chunk_text(text: str, size: int = 12) -> Iterator[str]:
    if not text:
        yield ""
        return
    for i in range(0, len(text), size):
        yield text[i : i + size]


def chat_stream_frames(job: Job) -> Iterator[str]:
    first = {
        "id": job.id,
        "object": "chat.completion.chunk",
        "created": job.created,
        "model": job.model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    }
    yield _sse(first)
    for piece in chunk_text(job.reply or ""):
        if not piece:
            continue
        yield _sse(
            {
                "id": job.id,
                "object": "chat.completion.chunk",
                "created": job.created,
                "model": job.model,
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
        )
    yield _sse(
        {
            "id": job.id,
            "object": "chat.completion.chunk",
            "created": job.created,
            "model": job.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": job.finish_reason}],
            "usage": job.usage,
        }
    )
    yield "data: [DONE]\n\n"


def completion_stream_frames(job: Job) -> Iterator[str]:
    for piece in chunk_text(job.reply or ""):
        yield _sse(
            {
                "id": job.id,
                "object": "text_completion",
                "created": job.created,
                "model": job.model,
                "choices": [{"index": 0, "text": piece, "finish_reason": None}],
            }
        )
    yield _sse(
        {
            "id": job.id,
            "object": "text_completion",
            "created": job.created,
            "model": job.model,
            "choices": [{"index": 0, "text": "", "finish_reason": job.finish_reason}],
            "usage": job.usage,
        }
    )
    yield "data: [DONE]\n\n"


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
