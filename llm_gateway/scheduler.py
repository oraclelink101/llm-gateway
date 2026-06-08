"""Priority scheduler with TRUE in-flight preemption.

Design
------
A single async *dispatcher* owns a fixed number of execution *slots*
(``concurrency``). Requests are submitted as :class:`Job` objects and held in a
priority-ordered pending list (FIFO within a priority tier).

The dispatcher does two things on every wake-up:

1. Fill any free slot with the highest-priority pending job.
2. If slots are full but a pending job outranks a currently *running* job, it
   **preempts** the lowest-priority running job: the running asyncio Task is
   cancelled, its job is **requeued** (not lost), and the freed slot is then
   given to the higher-priority job.

Preemption is *true* and *in-flight*: the backend call is an awaited
``asyncio.sleep`` (a real cancellation point), so cancelling the Task actually
interrupts work that is already executing -- it does not merely reorder the
queue. Interrupted jobs are restarted from scratch when rescheduled, and the
number of times each job was preempted is tracked.

A simple cumulative **budget cap** rejects requests that would exceed the
configured spend.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .backends import BackendError, BackendPool, estimate_tokens
from .models import ChatRequest, Priority
from .router import Router, RoutingDecision


class BudgetExceededError(RuntimeError):
    """Raised when a request would push cumulative spend over the budget cap."""


@dataclass
class SchedulerResult:
    """Everything the API layer needs to build a response + metadata."""

    content: str
    model: str
    routed_model: str
    complexity_score: float
    priority: Priority
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float          # successful backend service time
    queue_wait_ms: float       # cumulative time spent waiting (across requeues)
    preempted_count: int
    used_fallback: bool


@dataclass(order=False)
class Job:
    request: ChatRequest
    decision: RoutingDecision
    future: "asyncio.Future[SchedulerResult]"
    priority: Priority
    seq: int                          # global FIFO tie-breaker
    enqueue_t: float                  # when first submitted
    last_enqueue_t: float             # when (re)added to pending
    queue_wait_ms: float = 0.0
    preempted_count: int = 0
    being_preempted: bool = False
    id: str = field(default_factory=lambda: f"req-{uuid.uuid4().hex[:10]}")


@dataclass
class _Slot:
    job: Job
    task: "asyncio.Task"


class Scheduler:
    def __init__(
        self,
        pool: BackendPool,
        router: Router,
        *,
        concurrency: int = 2,
        budget_usd: Optional[float] = None,
    ):
        self.pool = pool
        self.router = router
        self.concurrency = concurrency
        self.budget_usd = budget_usd

        self._pending: list[Job] = []
        self._running: dict[int, _Slot] = {}
        self._slot_ids = itertools.count()
        self._seq = itertools.count()
        self._wakeup = asyncio.Event()
        self._dispatcher: Optional[asyncio.Task] = None
        self._closing = False

        # Observable stats (used by benchmarks/tests).
        self.spent_usd = 0.0
        self.preemption_count = 0

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if self._dispatcher is None:
            self._closing = False
            self._dispatcher = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._closing = True
        self._wakeup.set()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            try:
                await self._dispatcher
            except asyncio.CancelledError:
                pass
            self._dispatcher = None
        # Cancel any in-flight slots.
        for slot in list(self._running.values()):
            slot.task.cancel()

    # -- submission ---------------------------------------------------------
    async def submit(self, request: ChatRequest) -> SchedulerResult:
        loop = asyncio.get_running_loop()
        decision = self.router.route(request.prompt, request.model)
        now = loop.time()
        job = Job(
            request=request,
            decision=decision,
            future=loop.create_future(),
            priority=request.priority,
            seq=next(self._seq),
            enqueue_t=now,
            last_enqueue_t=now,
        )
        self._pending.append(job)
        self._wakeup.set()
        return await job.future

    # -- dispatcher ---------------------------------------------------------
    async def _dispatch_loop(self) -> None:
        while not self._closing:
            await self._wakeup.wait()
            self._wakeup.clear()
            if self._closing:
                break
            self._schedule()

    def _schedule(self) -> None:
        # 1) Fill free slots with the best pending jobs.
        while self._pending and len(self._running) < self.concurrency:
            self._start(self._pop_best())

        # 2) Preempt if a pending job outranks the lowest-priority running job.
        if not self._pending:
            return
        best = min(self._pending, key=lambda j: (j.priority.rank, j.seq))
        victim = self._lowest_priority_running()
        if victim is not None and best.priority.rank < victim.job.priority.rank:
            victim.job.being_preempted = True
            self.preemption_count += 1
            victim.task.cancel()
            # The cancelled task requeues its job and wakes us again; the next
            # _schedule pass will place `best` into the freed slot.

    def _pop_best(self) -> Job:
        best = min(self._pending, key=lambda j: (j.priority.rank, j.seq))
        self._pending.remove(best)
        return best

    def _lowest_priority_running(self) -> Optional[_Slot]:
        candidates = [s for s in self._running.values() if not s.job.being_preempted]
        if not candidates:
            return None
        # Lowest priority == highest rank; tie-break: most recently started (max seq).
        return max(candidates, key=lambda s: (s.job.priority.rank, s.job.seq))

    def _start(self, job: Job) -> None:
        loop = asyncio.get_running_loop()
        job.queue_wait_ms += (loop.time() - job.last_enqueue_t) * 1000.0
        slot_id = next(self._slot_ids)
        task = asyncio.create_task(self._run(job, slot_id))
        self._running[slot_id] = _Slot(job=job, task=task)

    async def _run(self, job: Job, slot_id: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            result = await self._execute(job)
        except asyncio.CancelledError:
            if job.being_preempted:
                # Preemption: requeue the job, do not complete the future.
                job.being_preempted = False
                job.preempted_count += 1
                job.last_enqueue_t = loop.time()
                self._pending.append(job)
                return
            # Genuine shutdown cancellation.
            if not job.future.done():
                job.future.cancel()
            raise
        except Exception as exc:  # backend/budget error -> surface to caller
            if not job.future.done():
                job.future.set_exception(exc)
        else:
            if not job.future.done():
                job.future.set_result(result)
        finally:
            self._running.pop(slot_id, None)
            self._wakeup.set()

    async def _execute(self, job: Job) -> SchedulerResult:
        loop = asyncio.get_running_loop()
        req = job.request
        model = job.decision.model
        prompt_tokens = estimate_tokens(req.prompt)

        # Budget check (estimate uses prompt + requested completion tokens).
        if self.budget_usd is not None:
            est = self.pool.get(model).cost_for(prompt_tokens, req.max_tokens)
            if self.spent_usd + est > self.budget_usd:
                raise BudgetExceededError(
                    f"budget cap ${self.budget_usd:.4f} would be exceeded "
                    f"(spent ${self.spent_usd:.4f}, est +${est:.4f})"
                )

        used_fallback = False
        start = loop.time()
        try:
            completion = await self.pool.get(model).chat(req.prompt, req.max_tokens)
        except BackendError:
            # Fallback to an alternate tier on simulated failure.
            model = self.router.fallback_model(model)
            used_fallback = True
            completion = await self.pool.get(model).chat(req.prompt, req.max_tokens)
        latency_ms = (loop.time() - start) * 1000.0

        usage = completion.usage
        cost = self.pool.get(model).cost_for(usage.prompt_tokens, usage.completion_tokens)
        self.spent_usd += cost

        return SchedulerResult(
            content=completion.choices[0].message.content,
            model=model,
            routed_model=job.decision.model,
            complexity_score=job.decision.complexity_score,
            priority=job.priority,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=cost,
            latency_ms=round(latency_ms, 3),
            queue_wait_ms=round(job.queue_wait_ms, 3),
            preempted_count=job.preempted_count,
            used_fallback=used_fallback,
        )
