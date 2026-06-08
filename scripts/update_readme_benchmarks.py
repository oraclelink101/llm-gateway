"""Run both benchmarks and inject their markdown tables into README.md.

The README contains marker pairs:
    <!-- ROUTING_BENCH:START --> ... <!-- ROUTING_BENCH:END -->
    <!-- SCHEDULER_BENCH:START --> ... <!-- SCHEDULER_BENCH:END -->
This script replaces the content between each pair with freshly generated,
simulated numbers from the project's own mock backends.

Usage:  python scripts/update_readme_benchmarks.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow running as `python scripts/update_readme_benchmarks.py`: put the project
# root (not scripts/) on sys.path so `benchmarks` and `llm_gateway` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks import routing_benchmark, scheduler_benchmark  # noqa: E402

README = Path(__file__).resolve().parent.parent / "README.md"


def _replace(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    replacement = f"{start}\n{body}\n{end}"
    if not pattern.search(text):
        raise SystemExit(f"Markers {name} not found in README.md")
    return pattern.sub(replacement, text)


def main() -> None:
    routing_md = routing_benchmark.format_markdown(routing_benchmark.run())
    scheduler_md = scheduler_benchmark.format_markdown(scheduler_benchmark.run())

    text = README.read_text(encoding="utf-8")
    text = _replace(text, "ROUTING_BENCH", routing_md)
    text = _replace(text, "SCHEDULER_BENCH", scheduler_md)
    README.write_text(text, encoding="utf-8")
    print("Updated README.md with fresh benchmark numbers.")
    print("\n--- routing ---\n" + routing_md)
    print("\n--- scheduler ---\n" + scheduler_md)


if __name__ == "__main__":
    main()
