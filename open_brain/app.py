from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from open_brain.openai_format import (
    chat_completion,
    chat_stream_frames,
    completion_stream_frames,
    estimate_prompt_tokens,
    message_text,
    openai_error,
    prompt_to_messages,
    text_completion,
)
from open_brain.schemas import ChatCompletionRequest, CompletionRequest, OperatorReject, OperatorReply
from open_brain.store import Job, JobStore

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_MODELS = [
    os.environ.get("OPEN_BRAIN_MODEL", "human"),
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4",
    "gpt-3.5-turbo",
    "o1",
    "o3",
]
API_KEY = os.environ.get("OPEN_BRAIN_API_KEY", "")
WAIT_TIMEOUT = float(os.environ.get("OPEN_BRAIN_WAIT_TIMEOUT", str(6 * 3600)))

store = JobStore(ttl_seconds=WAIT_TIMEOUT)
app = FastAPI(title="Open Brain", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return token or scheme
    return token.strip() or None


def require_api_key(authorization: str | None) -> None:
    if not API_KEY:
        return
    if _bearer(authorization) != API_KEY:
        raise HTTPException(
            status_code=401,
            detail=openai_error("Invalid API key", type_="invalid_request_error", code="invalid_api_key")["error"],
        )


def model_card(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "open-brain",
    }


async def wait_for_operator(job: Job, request: Request) -> Job:
    job.usage["prompt_tokens"] = estimate_prompt_tokens(job.messages)
    job.usage["total_tokens"] = job.usage["prompt_tokens"]

    async def _disconnected() -> None:
        while True:
            if await request.is_disconnected():
                store.cancel(job)
                return
            await asyncio.sleep(0.4)

    watcher = asyncio.create_task(_disconnected())
    try:
        await asyncio.wait_for(job.event.wait(), timeout=WAIT_TIMEOUT)
    except TimeoutError:
        if job.status == "pending":
            job.status = "expired"
            job.error = "request expired waiting for operator"
            job.event.set()
    finally:
        watcher.cancel()
    return job


def fail_if_unfinished(job: Job) -> JSONResponse | None:
    if job.status == "completed":
        return None
    mapping = {
        "rejected": (400, "invalid_request_error", "operator_rejected"),
        "expired": (504, "timeout", "timeout"),
        "cancelled": (499, "cancelled", "cancelled"),
        "pending": (504, "timeout", "timeout"),
    }
    status, err_type, code = mapping.get(job.status, (500, "server_error", "server_error"))
    return JSONResponse(
        openai_error(job.error or "request failed", type_=err_type, code=code),
        status_code=status,
    )


@app.get("/")
async def operator_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "pending": len(store.pending())}


@app.get("/v1/models")
async def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_key(authorization)
    models = list(dict.fromkeys(DEFAULT_MODELS))
    return {"object": "list", "data": [model_card(m) for m in models]}


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_key(authorization)
    return model_card(model_id)


@app.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_api_key(authorization)
    if not body.messages:
        return JSONResponse(openai_error("messages is required"), status_code=400)

    messages = [m.model_dump(exclude_none=True) for m in body.messages]
    job = store.create(
        kind="chat",
        model=body.model,
        stream=body.stream,
        payload=body.model_dump(exclude_none=True),
        messages=messages,
    )
    await wait_for_operator(job, request)
    failed = fail_if_unfinished(job)
    if failed is not None:
        return failed
    if body.stream:
        return StreamingResponse(iter(chat_stream_frames(job)), media_type="text/event-stream")
    return chat_completion(job)


@app.post("/v1/completions")
async def completions(
    body: CompletionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_api_key(authorization)
    messages = prompt_to_messages(body.prompt)
    job = store.create(
        kind="completion",
        model=body.model,
        stream=body.stream,
        payload=body.model_dump(exclude_none=True),
        messages=messages,
        prompt=message_text(body.prompt),
    )
    await wait_for_operator(job, request)
    failed = fail_if_unfinished(job)
    if failed is not None:
        return failed
    if body.stream:
        return StreamingResponse(iter(completion_stream_frames(job)), media_type="text/event-stream")
    return text_completion(job)


@app.get("/operator/jobs")
async def operator_jobs() -> dict[str, Any]:
    store.expire_stale()
    return {"jobs": [j.to_public() for j in store.recent()]}


@app.get("/operator/jobs/{job_id}")
async def operator_job(job_id: str) -> dict[str, Any]:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_public()


@app.post("/operator/jobs/{job_id}/reply")
async def operator_reply(job_id: str, body: OperatorReply) -> dict[str, Any]:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        store.complete(job, body.content, body.finish_reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_public()


@app.post("/operator/jobs/{job_id}/reject")
async def operator_reject(job_id: str, body: OperatorReject) -> dict[str, Any]:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        store.reject(job, body.message, body.code)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_public()


@app.websocket("/operator/ws")
async def operator_ws(ws: WebSocket) -> None:
    await ws.accept()
    queue = store.subscribe()
    try:
        await ws.send_json({"type": "hello", "pending": len(store.pending())})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                await ws.send_json(event)
            except TimeoutError:
                await ws.send_json({"type": "ping", "ts": int(time.time())})
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(queue)
