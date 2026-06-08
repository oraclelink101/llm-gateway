"""Routing benchmark: cost savings of cost-aware routing vs an all-large policy.

For a deterministic mixed workload, we compare:
  * routed   : the router picks small/large per prompt complexity,
  * all-large: every request is sent to the expensive `large` model.

Cost is computed by this project's own mock backends (constant $/1k tokens).

Run:  python -m benchmarks.routing_benchmark
"""

from __future__ import annotations

from llm_gateway.backends import BackendPool, estimate_tokens
from llm_gateway.router import Router

from ._common import make_workload

SEED = 1234
N = 500
COMPLEX_RATIO = 0.4


def run(n: int = N, complex_ratio: float = COMPLEX_RATIO, seed: int = SEED) -> dict:
    pool = BackendPool()
    router = Router()
    workload = make_workload(n, complex_ratio, seed)

    routed_cost = 0.0
    all_large_cost = 0.0
    n_small = n_large = 0

    large = pool.get("large")
    for item in workload:
        prompt_tokens = estimate_tokens(item.prompt)
        decision = router.route(item.prompt)
        chosen = pool.get(decision.model)
        routed_cost += chosen.cost_for(prompt_tokens, item.max_tokens)
        all_large_cost += large.cost_for(prompt_tokens, item.max_tokens)
        if decision.model == "small":
            n_small += 1
        else:
            n_large += 1

    savings_pct = (
        (all_large_cost - routed_cost) / all_large_cost * 100.0
        if all_large_cost
        else 0.0
    )
    return {
        "n": n,
        "complex_ratio": complex_ratio,
        "n_small": n_small,
        "n_large": n_large,
        "routed_cost": routed_cost,
        "all_large_cost": all_large_cost,
        "savings_pct": savings_pct,
    }


def format_markdown(r: dict) -> str:
    lines = [
        f"Workload: {r['n']} requests, {int(r['complex_ratio'] * 100)}% complex "
        f"(seed={SEED}). Routed to small: {r['n_small']}, large: {r['n_large']}.",
        "",
        "| Policy | Total cost (USD) | Cost vs all-large |",
        "|---|---:|---:|",
        f"| All-large (baseline) | ${r['all_large_cost']:.4f} | 100.0% |",
        f"| Cost-aware routing | ${r['routed_cost']:.4f} | "
        f"{r['routed_cost'] / r['all_large_cost'] * 100:.1f}% |",
        "",
        f"**Cost savings: {r['savings_pct']:.1f}%** (simulated).",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_markdown(run()))
