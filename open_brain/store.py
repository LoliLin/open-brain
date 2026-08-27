from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

JobKind = Literal["chat", "completion"]
JobStatus = Literal["pending", "completed", "rejected", "expired", "cancelled"]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


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
            "finish_reason": self.finish_reason,
            "error": self.error,
            "usage": self.usage,
            "payload": self.payload,
        }


class JobStore:
    def __init__(self, ttl_seconds: float = 6 * 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()

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

    def complete(self, job: Job, content: str, finish_reason: str = "stop") -> None:
        if job.status != "pending":
            raise ValueError(f"job {job.id} is {job.status}")
        job.reply = content
        job.finish_reason = finish_reason
        job.status = "completed"
        job.usage["completion_tokens"] = max(1, len(content) // 4)
        job.usage["total_tokens"] = job.usage["prompt_tokens"] + job.usage["completion_tokens"]
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
