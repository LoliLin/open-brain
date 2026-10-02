from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

JobKind = Literal["chat", "completion"]
JobStatus = Literal["pending", "completed", "rejected", "expired", "cancelled"]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def message_fingerprint(msg: dict[str, Any]) -> str:
    key = {
        "role": msg.get("role"),
        "content": msg.get("content"),
        "name": msg.get("name"),
        "tool_call_id": msg.get("tool_call_id"),
        "tool_calls": msg.get("tool_calls"),
    }
    return json.dumps(key, sort_keys=True, ensure_ascii=False)


def sequence_key(fingerprints: list[str]) -> str:
    return "|||".join(fingerprints)


@dataclass
class Job:
    id: str
    kind: JobKind
    model: str
    created: int
    stream: bool
    payload: dict[str, Any]
    messages: list[dict[str, Any]]
    prompt: str | None = None
    status: JobStatus = "pending"
    reply: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"
    error: str | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    event: asyncio.Event = field(default_factory=asyncio.Event)
    prefix_match: dict[str, Any] | None = None
    exact_match: dict[str, Any] | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "model": self.model,
            "created": self.created,
            "stream": self.stream,
            "status": self.status,
            "messages": self.messages,
            "prompt": self.prompt,
            "reply": self.reply,
            "tool_calls": self.tool_calls,
            "finish_reason": self.finish_reason,
            "error": self.error,
            "usage": self.usage,
            "payload": self.payload,
            "prefix_match": self.prefix_match,
            "exact_match": self.exact_match,
        }


class JobStore:
    def __init__(self, ttl_seconds: float = 6 * 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._exact_cache: dict[str, dict[str, Any]] = {}
        self._completed_history: list[tuple[list[str], str]] = []
        self._snippets: list[dict[str, str]] = [
            {"id": "snip-1", "title": "好的/处理中", "content": "好的，收到您的请求，正在为您处理中。"},
            {"id": "snip-2", "title": "完成确认", "content": "已为您处理完成，如有其他问题请随时告诉我。"},
            {"id": "snip-3", "title": "补充细节", "content": "请提供更多上下文或具体细节，以便我能更准确地回答。"},
            {"id": "snip-4", "title": "无法执行", "content": "抱歉，作为人工协助助手，该操作超出当前权限范围，无法直接执行。"},
        ]

    def create(
        self,
        *,
        kind: JobKind,
        model: str,
        stream: bool,
        payload: dict[str, Any],
        messages: list[dict[str, Any]],
        prompt: str | None = None,
    ) -> Job:
        job = Job(
            id=new_id("chatcmpl" if kind == "chat" else "cmpl"),
            kind=kind,
            model=model,
            created=int(time.time()),
            stream=stream,
            payload=payload,
            messages=messages,
            prompt=prompt,
        )

        fps = [message_fingerprint(m) for m in messages]
        seq_key = sequence_key(fps)

        if seq_key in self._exact_cache:
            cached = self._exact_cache[seq_key]
            job.exact_match = {
                "matched_job_id": cached.get("job_id"),
                "reply": cached.get("reply"),
                "tool_calls": cached.get("tool_calls"),
                "finish_reason": cached.get("finish_reason", "stop"),
                "created": cached.get("created"),
            }

        longest_k = 0
        matched_job_id = None
        for hist_fps, hist_job_id in reversed(self._completed_history):
            k = 0
            while k < len(fps) and k < len(hist_fps) and fps[k] == hist_fps[k]:
                k += 1
            if k > longest_k and k < len(fps):
                longest_k = k
                matched_job_id = hist_job_id

        if longest_k > 0 and matched_job_id:
            job.prefix_match = {
                "matched_job_id": matched_job_id,
                "prefix_count": longest_k,
                "total_count": len(fps),
            }
        elif len(fps) > 2:
            job.prefix_match = {
                "matched_job_id": None,
                "prefix_count": len(fps) - 1,
                "total_count": len(fps),
            }

        self._jobs[job.id] = job
        self._broadcast({"type": "created", "job": job.to_public()})
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def pending(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status == "pending"]

    def recent(self, limit: int = 50) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return jobs[:limit]

    def complete(
        self,
        job: Job,
        content: str | None = "",
        finish_reason: str = "stop",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        if job.status != "pending":
            raise ValueError(f"job {job.id} is {job.status}")
        job.reply = content or ""
        job.tool_calls = tool_calls
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_calls"
        job.finish_reason = finish_reason
        job.status = "completed"

        reply_len = len(content or "")
        if tool_calls:
            reply_len += len(json.dumps(tool_calls, ensure_ascii=False))
        job.usage["completion_tokens"] = max(1, reply_len // 4)
        job.usage["total_tokens"] = job.usage["prompt_tokens"] + job.usage["completion_tokens"]

        fps = [message_fingerprint(m) for m in job.messages]
        if fps:
            seq_key = sequence_key(fps)
            self._exact_cache[seq_key] = {
                "job_id": job.id,
                "reply": job.reply,
                "tool_calls": job.tool_calls,
                "finish_reason": job.finish_reason,
                "created": int(time.time()),
            }
            self._completed_history.append((fps, job.id))

        job.event.set()
        self._broadcast({"type": "completed", "job": job.to_public()})

    def reject(self, job: Job, message: str, code: str = "operator_rejected") -> None:
        if job.status != "pending":
            raise ValueError(f"job {job.id} is {job.status}")
        job.status = "rejected"
        job.error = message
        job.finish_reason = code
        job.event.set()
        self._broadcast({"type": "rejected", "job": job.to_public()})

    def cancel(self, job: Job) -> None:
        if job.status != "pending":
            return
        job.status = "cancelled"
        job.error = "client disconnected"
        job.event.set()
        self._broadcast({"type": "cancelled", "job": job.to_public()})

    def expire_stale(self) -> None:
        now = time.time()
        for job in list(self._jobs.values()):
            if job.status == "pending" and now - job.created > self.ttl_seconds:
                job.status = "expired"
                job.error = "request expired waiting for operator"
                job.event.set()
                self._broadcast({"type": "expired", "job": job.to_public()})

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._listeners.discard(queue)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def get_snippets(self) -> list[dict[str, str]]:
        return list(self._snippets)

    def add_snippet(self, title: str, content: str) -> dict[str, str]:
        snip = {"id": f"snip-{uuid.uuid4().hex[:8]}", "title": title, "content": content}
        self._snippets.append(snip)
        return snip

    def delete_snippet(self, snippet_id: str) -> bool:
        initial = len(self._snippets)
        self._snippets = [s for s in self._snippets if s["id"] != snippet_id]
        return len(self._snippets) < initial

    def get_cache_info(self) -> dict[str, Any]:
        return {
            "exact_cache_size": len(self._exact_cache),
            "history_size": len(self._completed_history),
        }

    def clear_cache(self) -> None:
        self._exact_cache.clear()
        self._completed_history.clear()
