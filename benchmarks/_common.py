"""Shared helpers for benchmarks: deterministic workload generation + stats.

All workloads are seeded so results are reproducible. Every reported number is
produced by this project's own mock backends -- nothing here references any
real vendor, price, or latency.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class WorkItem:
    prompt: str
    max_tokens: int


# Pools of simple vs complex prompt fragments used to build a mixed workload.
_SIMPLE = [
    "Translate 'hello' to French.",
    "What is 2 + 2?",
    "Give me a synonym for happy.",
    "Capital of Japan?",
    "Convert 10 km to miles.",
    "Define the word 'cat'.",
    "What day comes after Monday?",
    "Spell 'banana'.",
]

_COMPLEX = [
    "Analyze the time complexity of quicksort and explain the worst case step by step.",
    "Design a fault-tolerant architecture for a distributed key-value store and compare trade-offs.",
    "Debug and refactor this algorithm:\n```\nfor i in range(n):\n  for j in range(n):\n    ...\n```",
    "Prove that the square root of 2 is irrational, deriving each step.",
    "Compare and optimize two approaches to caching, explaining the reasoning.",
    "Explain the architecture of a transformer model and analyze its bottlenecks.",
]


def make_workload(n: int, complex_ratio: float, seed: int) -> list[WorkItem]:
    """Generate `n` work items; `complex_ratio` fraction are complex prompts."""
    rng = random.Random(seed)
    items: list[WorkItem] = []
    for _ in range(n):
        if rng.random() < complex_ratio:
            prompt = rng.choice(_COMPLEX)
            max_tokens = rng.randint(200, 400)
        else:
            prompt = rng.choice(_SIMPLE)
            max_tokens = rng.randint(16, 64)
        items.append(WorkItem(prompt=prompt, max_tokens=max_tokens))
    return items


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0,100])."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
