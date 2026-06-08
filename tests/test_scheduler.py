"""Scheduler tests: priority ordering, TRUE preemption, fallback, budget cap."""

import asyncio

import pytest

from llm_gateway.backends import BackendPool
from llm_gateway.models import ChatRequest, Priority
from llm_gateway.router import Router
from llm_gateway.scheduler import BudgetExceededError, Scheduler


async def test_basic_submit_returns_result():
    sched = Scheduler(BackendPool(speed_factor=0.2), Router(), concurrency=2)
    await sched.start()
    res = await sched.submit(
        ChatRequest(prompt="What is 2 + 2?", priority=Priority.NORMAL, max_tokens=16)
    )
    await sched.stop()
    assert res.content
    assert res.model == "small"
    assert res.cost_usd > 0


async def test_true_preemption_high_interrupts_low():
    """A HIGH request must interrupt a running LOW request, which is requeued."""
    sched = Scheduler(BackendPool(speed_factor=1.0), Router(), concurrency=1)
    await sched.start()
    completion_order: list[str] = []

    async def run_low():
        res = await sched.submit(
            ChatRequest(prompt="x" * 12, priority=Priority.LOW,
                        model="large", max_tokens=200)
        )
        completion_order.append("low")
        return res

    async def run_high():
        await asyncio.sleep(0.02)  # let LOW start running first
        res = await sched.submit(
            ChatRequest(prompt="hi", priority=Priority.HIGH,
                        model="small", max_tokens=16)
        )
        completion_order.append("high")
        return res

    low_res, _high_res = await asyncio.gather(run_low(), run_high())
    await sched.stop()

    assert sched.preemption_count >= 1           # preemption actually happened
    assert low_res.preempted_count >= 1          # the LOW job was requeued
    assert completion_order[0] == "high"         # HIGH finished first despite arriving later


async def test_priority_queue_order_when_busy():
    """When the worker is busy, queued jobs are served by priority."""
    sched = Scheduler(BackendPool(speed_factor=0.2), Router(), concurrency=1)
    await sched.start()
    done: list[str] = []

    async def job(tag, prio, model, mt):
        await sched.submit(
            ChatRequest(prompt="hello", priority=prio, model=model, max_tokens=mt)
        )
        done.append(tag)

    blocker = asyncio.create_task(job("blocker", Priority.HIGH, "large", 400))
    await asyncio.sleep(0.03)  # ensure blocker is running
    low = asyncio.create_task(job("low", Priority.LOW, "small", 16))
    await asyncio.sleep(0.005)
    normal = asyncio.create_task(job("normal", Priority.NORMAL, "small", 16))
    await asyncio.sleep(0.005)
    high = asyncio.create_task(job("high", Priority.HIGH, "small", 16))

    await asyncio.gather(blocker, low, normal, high)
    await sched.stop()

    assert done[0] == "blocker"
    assert done.index("high") < done.index("normal") < done.index("low")


async def test_fallback_on_backend_failure():
    pool = BackendPool(failure_rates={"large": 1.0})  # large always fails
    sched = Scheduler(pool, Router(), concurrency=1)
    await sched.start()
    res = await sched.submit(
        ChatRequest(prompt="analyze and design architecture step by step",
                    priority=Priority.NORMAL, model="large", max_tokens=50)
    )
    await sched.stop()
    assert res.used_fallback is True
    assert res.model == "small"


async def test_budget_cap_rejects():
    sched = Scheduler(BackendPool(), Router(), concurrency=2, budget_usd=0.0001)
    await sched.start()
    with pytest.raises(BudgetExceededError):
        await sched.submit(
            ChatRequest(prompt="hello", model="large", max_tokens=500)
        )
    await sched.stop()
