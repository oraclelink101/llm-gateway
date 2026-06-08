"""Router tests: complexity scoring picks the right model + fallback works."""

from llm_gateway.router import Router


def test_simple_prompt_routes_small():
    r = Router()
    decision = r.route("What is 2 + 2?")
    assert decision.model == "small"
    assert 0.0 <= decision.complexity_score < r.config.complexity_threshold


def test_complex_prompt_routes_large():
    r = Router()
    prompt = (
        "Analyze and design a fault-tolerant architecture, compare trade-offs, "
        "and explain the algorithm step by step with reasoning."
    )
    decision = r.route(prompt)
    assert decision.model == "large"
    assert decision.complexity_score >= r.config.complexity_threshold


def test_code_marker_increases_score():
    r = Router()
    plain = r.score("fix this")
    with_code = r.score("fix this\n```\nfor i in range(n): pass\n```")
    assert with_code > plain


def test_long_prompt_increases_score():
    r = Router()
    short = r.score("hello")
    long = r.score("word " * 300)
    assert long > short


def test_override_forces_model():
    r = Router()
    decision = r.route("What is 2 + 2?", override="large")
    assert decision.model == "large"


def test_fallback_is_different_tier():
    r = Router()
    assert r.fallback_model("large") != "large"


def test_score_is_bounded():
    r = Router()
    prompt = ("analyze explain prove derive design compare optimize debug "
              "refactor architecture algorithm " * 20) + "\n```\ncode\n```"
    s = r.score(prompt)
    assert 0.0 <= s <= 1.0
