"""Scheduler benchmark: high-priority tail latency, FCFS vs priority+preemption.

A backlog of long-running LOW-priority requests (routed to the slow `large`
model) is generated, and a smaller set of fast HIGH-priority requests arrives
in the middle of that backlog. We measure the HIGH tier's end-to-end latency
(queue wait + service, including any preemption restarts) under two policies:

  * FCFS     : priority ignored -> first-come-first-served (all NORMAL).
  * Priority : the real scheduler -> HIGH preempts running LOW work.

Latency is produced by this project's own mock backends. A `speed_factor`
scales simulated time down so the benchmark finishes quickly; the *ratio*
between policies is what matters.

Run:  python -m benchmarks.scheduler_benchmark
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from llm_gateway.backends import BackendPool
from llm_gateway.models import ChatRequest, Priority
from llm_gateway.router import Router
from llm_gateway.scheduler import Scheduler

from ._common import mean, percentile

SEED = 7
SPEED_FACTOR = 0.1          # scale simulated latency to keep wall time small
CONCURRENCY = 2
N_BACKGROUND = 48           # LOW-priority long jobs forming the backlog
N_HIGH = 12                 # HIGH-priority short jobs
ARRIVAL_GAP_S = 0.004       # stagger submissions to build a queue

_LONG_PROMPT = (
    "Analyze and design a fault-tolerant distributed system, comparing trade-offs "
    "step by step and explaining the reasoning behind each architectural choice."
)
_SHORT_PROMPT = "What is 2 + 2?"


@dataclass
class _Arrival:
    order: int          # arrival index (controls FCFS ordering)
    is_high: bool
    prompt: str
    max_tokens: int


def _build_arrivals(seed: int) -> list[_Arrival]:
    """Interleave HIGH arrivals within the LOW backlog deterministically."""
    rng = random.Random(seed)
    arrivals: list[_Arrival] = []
    for _ in range(N_BACKGROUND):
        arrivals.append(_Arrival(0, False, _LONG_PROMPT, rng.randint(250, 350)))
    for _ in range(N_HIGH):
        arrivals.append(_Arrival(0, True, _SHORT_PROMPT, rng.randint(16, 48)))
    rng.shuffle(arrivals)
    # Ensure HIGH jobs are not all at the very front: keep shuffled order but
    # renumber arrival order sequentially.
    for i, a in enumerate(arrivals):
        a.order = i
    return arrivals


async def _run_policy(priority_enabled: bool, seed: int = SEED) -> dict:
    pool = BackendPool(speed_factor=SPEED_FACTOR)
    sched = Scheduler(pool, Router(), concurrency=CONCURRENCY)
    await sched.start()

    arrivals = _build_arrivals(seed)
    high_latencies: list[float] = []
    all_latencies: list[float] = []

    async def submit_one(a: _Arrival) -> None:
        await asyncio.sleep(a.order * ARRIVAL_GAP_S)
        if priority_enabled:
            prio = Priority.HIGH if a.is_high else Priority.LOW
        else:
            prio = Priority.NORMAL  # FCFS: ignore priority
        req = ChatRequest(prompt=a.prompt, priority=prio, max_tokens=a.max_tokens)
        t0 = time.perf_counter()
        await sched.submit(req)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        all_latencies.append(latency_ms)
        if a.is_high:
            high_latencies.append(latency_ms)

    await asyncio.gather(*(submit_one(a) for a in arrivals))
    await sched.stop()

    return {
        "policy": "Priority + preemption" if priority_enabled else "FCFS",
        "high_p99_ms": percentile(high_latencies, 99),
        "high_p50_ms": percentile(high_latencies, 50),
        "high_mean_ms": mean(high_latencies),
        "all_p99_ms": percentile(all_latencies, 99),
        "preemptions": sched.preemption_count,
    }


def run(seed: int = SEED) -> dict:
    fcfs = asyncio.run(_run_policy(False, seed))
    prio = asyncio.run(_run_policy(True, seed))
    improvement = (
        (fcfs["high_p99_ms"] - prio["high_p99_ms"]) / fcfs["high_p99_ms"] * 100.0
        if fcfs["high_p99_ms"]
        else 0.0
    )
    return {"fcfs": fcfs, "priority": prio, "p99_improvement_pct": improvement}


def format_markdown(r: dict) -> str:
    f, p = r["fcfs"], r["priority"]
    lines = [
        f"Workload: {N_BACKGROUND} LOW background jobs + {N_HIGH} HIGH jobs, "
        f"concurrency={CONCURRENCY}, speed_factor={SPEED_FACTOR} (seed={SEED}).",
        "",
        "| Policy | HIGH p50 (ms) | HIGH p99 (ms) | HIGH mean (ms) | Preemptions |",
        "|---|---:|---:|---:|---:|",
        f"| FCFS | {f['high_p50_ms']:.1f} | {f['high_p99_ms']:.1f} | "
        f"{f['high_mean_ms']:.1f} | {f['preemptions']} |",
        f"| Priority + preemption | {p['high_p50_ms']:.1f} | {p['high_p99_ms']:.1f} | "
        f"{p['high_mean_ms']:.1f} | {p['preemptions']} |",
        "",
        f"**HIGH-tier p99 latency reduced by {r['p99_improvement_pct']:.1f}%** "
        f"with priority + true preemption (simulated).",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_markdown(run()))
