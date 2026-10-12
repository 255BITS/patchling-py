"""Live relevance-filter check. Skipped unless LIVE_LLM=1.

CI runs the mocked tests only. This module calls the configured
OpenAI-compatible endpoint with GPTDIFF_LLM_API_KEY and does not run in CI.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_LLM") != "1",
    reason="set LIVE_LLM=1 to call the live LLM",
)


def test_live_filter_relevant_yes_and_no():
    api_key = os.getenv("GPTDIFF_LLM_API_KEY")
    assert api_key, "GPTDIFF_LLM_API_KEY is required when LIVE_LLM=1"

    from patchling import filter_relevant

    files = {
        "calc.py": "def add(a, b):\n    \"\"\"Add two numbers.\"\"\"\n    return a + b\n",
        "cheese.md": "# Cheese notes\n\nGouda is not a programming language.\n",
    }
    result = filter_relevant(
        files,
        "Rename add() to sum_numbers() and update its docstring.",
        cache=False,
        concurrency=2,
    )
    by_path = {decision.path: decision for decision in result.decisions}
    assert set(by_path) == {"calc.py", "cheese.md"}
    assert by_path["calc.py"].error is None
    assert by_path["calc.py"].decision == "yes"
    assert by_path["calc.py"].tokens > 0
    assert "calc.py" in result.files
    assert by_path["cheese.md"].decision == "no"
    assert "cheese.md" not in result.files
