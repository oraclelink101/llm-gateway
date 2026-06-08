"""Mock LLM backends.

These simulate real model serving without any GPU or API key:
  * latency is produced with asyncio.sleep (so it is cancellable -> enables
    true preemption in the scheduler),
  * cost is a constant $/1k tokens per model tier,
  * "quality" is a static descriptor used only for documentation/benchmarks.

The interface is OpenAI-compatible: callers get back an OpenAIChatCompletion.
All numbers are simulation parameters, not real vendor figures.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass

from .models import (
    OpenAIChatCompletion,
    OpenAIChoice,
    OpenAIMessage,
    OpenAIUsage,
)


def estimate_tokens(text: str) -> int:
    """Very rough token estimate (~1 token per 4 chars), min 1."""
    return max(1, len(text) // 4)


class BackendError(RuntimeError):
    """Raised when a backend simulates a transient failure."""


@dataclass
class ModelSpec:
    """Static description of a virtual model tier."""

    name: str
    # Simulated service time = base_latency_ms + per_token_latency_ms * tokens.
    base_latency_ms: float
    per_token_latency_ms: float
    # Constant cost per 1k tokens (prompt + completion), in USD.
    cost_per_1k_tokens: float
    quality: str
    # Probability that a single call simulates a transient failure.
    failure_rate: float = 0.0


# Two virtual models. "large" is slower + pricier but higher quality.
# These parameters are arbitrary simulation values local to this project.
MODEL_SPECS: dict[str, ModelSpec] = {
    "small": ModelSpec(
        name="mock-small",
        base_latency_ms=20.0,
        per_token_latency_ms=0.4,
        cost_per_1k_tokens=0.20,
        quality="good-enough",
    ),
    "large": ModelSpec(
        name="mock-large",
        base_latency_ms=80.0,
        per_token_latency_ms=1.6,
        cost_per_1k_tokens=2.00,
        quality="high",
    ),
}


class MockBackend:
    """A simulated, OpenAI-compatible model backend for one tier."""

    def __init__(self, spec: ModelSpec, *, rng: random.Random | None = None,
                 speed_factor: float = 1.0):
        """speed_factor scales simulated latency (use <1 to run benchmarks fast)."""
        self.spec = spec
        self._rng = rng or random.Random()
        self.speed_factor = speed_factor

    def cost_for(self, prompt_tokens: int, completion_tokens: int) -> float:
        total = prompt_tokens + completion_tokens
        return round(self.spec.cost_per_1k_tokens * total / 1000.0, 6)

    def service_time_ms(self, completion_tokens: int) -> float:
        return (
            self.spec.base_latency_ms
            + self.spec.per_token_latency_ms * completion_tokens
        ) * self.speed_factor

    async def chat(self, prompt: str, max_tokens: int) -> OpenAIChatCompletion:
        """Simulate one chat completion.

        The await on asyncio.sleep is the cancellation point that lets the
        scheduler truly preempt an in-flight request.
        """
        if self.spec.failure_rate and self._rng.random() < self.spec.failure_rate:
            raise BackendError(f"{self.spec.name} simulated transient failure")

        prompt_tokens = estimate_tokens(prompt)
        # Pretend the model generated up to max_tokens.
        completion_tokens = max_tokens

        latency_ms = self.service_time_ms(completion_tokens)
        await asyncio.sleep(latency_ms / 1000.0)

        content = (
            f"[{self.spec.name}] simulated reply "
            f"({completion_tokens} tokens, quality={self.spec.quality})"
        )
        return OpenAIChatCompletion(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=self.spec.name,
            choices=[OpenAIChoice(message=OpenAIMessage(content=content))],
            usage=OpenAIUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )


class BackendPool:
    """Holds one MockBackend per configured tier."""

    def __init__(self, *, speed_factor: float = 1.0, rng: random.Random | None = None,
                 failure_rates: dict[str, float] | None = None):
        failure_rates = failure_rates or {}
        self._backends: dict[str, MockBackend] = {}
        for tier, spec in MODEL_SPECS.items():
            # Allow per-tier failure-rate overrides (used to demo fallback).
            eff_spec = spec
            if tier in failure_rates:
                eff_spec = ModelSpec(**{**spec.__dict__, "failure_rate": failure_rates[tier]})
            self._backends[tier] = MockBackend(
                eff_spec, rng=rng, speed_factor=speed_factor
            )

    def get(self, tier: str) -> MockBackend:
        if tier not in self._backends:
            raise KeyError(f"Unknown backend tier: {tier}")
        return self._backends[tier]

    def tiers(self) -> list[str]:
        return list(self._backends.keys())
