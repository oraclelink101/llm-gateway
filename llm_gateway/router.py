"""Cost-aware router.

Scores prompt complexity with a transparent heuristic and selects a model
tier. Simple prompts go to the cheap `small` model; complex ones go to the
pricier `large` model. Thresholds, weights and keywords are YAML-configurable.

The heuristic is intentionally simple and explainable (no ML): it combines
prompt length, presence of reasoning keywords, and code markers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .backends import estimate_tokens

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "routing.yaml"


@dataclass
class RouterConfig:
    complexity_threshold: float
    fallback: str
    length_saturation: int
    length_weight: float
    keyword_weight: float
    keyword_weight_cap: float
    code_marker_weight: float
    reasoning_keywords: list[str]
    models: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "RouterConfig":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        scoring = raw["scoring"]
        return cls(
            complexity_threshold=float(raw["complexity_threshold"]),
            fallback=str(raw["fallback"]),
            length_saturation=int(scoring["length_saturation"]),
            length_weight=float(scoring["length_weight"]),
            keyword_weight=float(scoring["keyword_weight"]),
            keyword_weight_cap=float(scoring["keyword_weight_cap"]),
            code_marker_weight=float(scoring["code_marker_weight"]),
            reasoning_keywords=[k.lower() for k in scoring["reasoning_keywords"]],
            models=raw["models"],
        )


@dataclass
class RoutingDecision:
    model: str
    complexity_score: float


class Router:
    """Selects a model tier from a prompt using the configured heuristic."""

    def __init__(self, config: RouterConfig | None = None):
        self.config = config or RouterConfig.from_yaml()

    def score(self, prompt: str) -> float:
        """Return a complexity score in [0, 1]."""
        cfg = self.config
        text = prompt.lower()

        # 1) Length component: saturating ratio of tokens to saturation point.
        tokens = estimate_tokens(prompt)
        length_ratio = min(1.0, tokens / cfg.length_saturation)
        length_component = length_ratio * cfg.length_weight

        # 2) Keyword component: each reasoning keyword adds weight, capped.
        hits = sum(1 for kw in cfg.reasoning_keywords if kw in text)
        keyword_component = min(cfg.keyword_weight_cap, hits * cfg.keyword_weight)

        # 3) Code marker component: presence of a fenced code block.
        code_component = cfg.code_marker_weight if "```" in prompt else 0.0

        score = length_component + keyword_component + code_component
        return round(min(1.0, score), 4)

    def route(self, prompt: str, override: str | None = None) -> RoutingDecision:
        """Pick a model. `override` forces a specific tier if valid."""
        if override:
            if override not in self.config.models:
                raise ValueError(f"Unknown model override: {override}")
            return RoutingDecision(model=override, complexity_score=self.score(prompt))

        score = self.score(prompt)
        model = "large" if score >= self.config.complexity_threshold else "small"
        return RoutingDecision(model=model, complexity_score=score)

    def fallback_model(self, failed_model: str) -> str:
        """Return a different tier to retry on after a backend failure."""
        fb = self.config.fallback
        if fb == failed_model:
            # Pick any other configured model.
            for tier in self.config.models:
                if tier != failed_model:
                    return tier
        return fb
