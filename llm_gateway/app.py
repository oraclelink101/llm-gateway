"""FastAPI application wiring router + scheduler + mock backends.

POST /v1/chat : {prompt, priority, max_tokens} -> router -> scheduler ->
backend -> {content, metadata}.

Run with:  uvicorn llm_gateway.app:app --reload
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .backends import BackendPool
from .models import ChatRequest, ChatResponse, ResponseMetadata
from .router import Router
from .scheduler import BudgetExceededError, Scheduler


def build_scheduler() -> Scheduler:
    """Construct the scheduler from environment-tunable settings."""
    concurrency = int(os.getenv("GATEWAY_CONCURRENCY", "2"))
    budget_env = os.getenv("GATEWAY_BUDGET_USD")
    budget = float(budget_env) if budget_env else None
    pool = BackendPool()
    router = Router()
    return Scheduler(pool, router, concurrency=concurrency, budget_usd=budget)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = build_scheduler()
    await app.state.scheduler.start()
    try:
        yield
    finally:
        await app.state.scheduler.stop()


app = FastAPI(
    title="llm-gateway",
    version="0.1.0",
    description="Cost-aware routing + priority scheduling with true preemption (mock backends).",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    sched: Scheduler = app.state.scheduler
    return {
        "status": "ok",
        "concurrency": sched.concurrency,
        "spent_usd": round(sched.spent_usd, 6),
        "budget_usd": sched.budget_usd,
        "preemption_count": sched.preemption_count,
    }


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    sched: Scheduler = app.state.scheduler
    t0 = time.perf_counter()
    try:
        result = await sched.submit(req)
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    total_ms = (time.perf_counter() - t0) * 1000.0

    return ChatResponse(
        id=f"chat-{uuid.uuid4().hex[:12]}",
        content=result.content,
        metadata=ResponseMetadata(
            model=result.model,
            routed_model=result.routed_model,
            complexity_score=result.complexity_score,
            priority=result.priority,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            queue_wait_ms=result.queue_wait_ms,
            total_ms=round(total_ms, 3),
            preempted_count=result.preempted_count,
            used_fallback=result.used_fallback,
        ),
    )
