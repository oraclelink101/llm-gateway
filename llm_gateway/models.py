"""Pydantic models and shared enums for the gateway.

These define the public request/response contract of POST /v1/chat as well as
the internal metadata that travels with a request through router -> scheduler
-> backend.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Priority(str, Enum):
    """Request priority tiers used by the scheduler.

    Ordering matters: HIGH preempts NORMAL and LOW; NORMAL preempts LOW.
    """

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def rank(self) -> int:
        """Lower rank == higher priority (0 is most important)."""
        return {Priority.HIGH: 0, Priority.NORMAL: 1, Priority.LOW: 2}[self]


class ChatRequest(BaseModel):
    """Public input to POST /v1/chat."""

    prompt: str = Field(..., min_length=1, description="User prompt.")
    priority: Priority = Field(
        default=Priority.NORMAL, description="Scheduling priority tier."
    )
    max_tokens: int = Field(
        default=256, ge=1, le=8192, description="Max tokens to generate."
    )
    # Optional override; if None, the router decides.
    model: Optional[str] = Field(
        default=None, description="Force a model ('small'/'large'). Router decides if None."
    )


class ResponseMetadata(BaseModel):
    """Diagnostics describing how the request was handled."""

    model: str = Field(..., description="Model actually used.")
    routed_model: str = Field(..., description="Model the router originally selected.")
    complexity_score: float = Field(..., description="Heuristic complexity score [0,1].")
    priority: Priority
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = Field(..., description="Simulated cost for this request.")
    latency_ms: float = Field(..., description="Backend service time (excludes queue wait).")
    queue_wait_ms: float = Field(..., description="Time spent waiting in the scheduler queue.")
    total_ms: float = Field(..., description="End-to-end time inside the gateway.")
    preempted_count: int = Field(
        default=0, description="How many times this request was preempted and requeued."
    )
    used_fallback: bool = Field(
        default=False, description="True if the primary backend failed and fallback was used."
    )


class ChatResponse(BaseModel):
    """Public output of POST /v1/chat."""

    id: str
    content: str
    metadata: ResponseMetadata


# --- OpenAI-compatible shapes (subset) -------------------------------------
# A minimal, OpenAI-style chat-completion response so existing clients can read
# `choices[0].message.content`. This is a simulation, not a real OpenAI call.


class OpenAIMessage(BaseModel):
    role: str = "assistant"
    content: str


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatCompletion(BaseModel):
    """OpenAI-compatible chat completion object returned by the mock backend."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage
