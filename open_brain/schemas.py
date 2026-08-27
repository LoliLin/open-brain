from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: Any | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "human"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    n: int = 1
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class CompletionRequest(BaseModel):
    model: str = "human"
    prompt: Any = ""
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    n: int = 1
    user: str | None = None
    suffix: str | None = None
    echo: bool = False

    model_config = {"extra": "allow"}


class OperatorReply(BaseModel):
    content: str
    finish_reason: Literal["stop", "length", "content_filter"] = "stop"
    role: str = "assistant"


class OperatorReject(BaseModel):
    message: str = "operator rejected the request"
    code: str = "operator_rejected"


class ErrorBody(BaseModel):
    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
