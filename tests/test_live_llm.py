"""Opt-in end-to-end smartapply cases against a live OpenAI-compatible API.

Default CI does not set LIVE_LLM, so these tests skip and make no network
calls. To run them:

    LIVE_LLM=1 GPTDIFF_LLM_API_KEY=... pytest tests/test_live_llm.py

GPTDIFF_LLM_BASE_URL overrides the endpoint (default https://nano-gpt.com/api/v1/).
LIVE_LLM_MODEL, GPTDIFF_SMARTAPPLY_MODEL, or GPTDIFF_MODEL selects the model.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from patchling import smartapply

CASES = json.loads((Path(__file__).parent / "fixtures" / "live_cases.json").read_text())

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_LLM") != "1",
    reason="live LLM tests are opt-in; set LIVE_LLM=1 and GPTDIFF_LLM_API_KEY",
)


def _grade(case, files):
    why = ""
    for path, needles in case["expect"].items():
        if path not in files or files[path] is None:
            why += f"missing {path}; "
            continue
        for needle in needles:
            if needle not in files[path]:
                why += f"{path} lacks {needle!r}; "
    for path, needles in case.get("reject", {}).items():
        for needle in needles:
            if path in files and files[path] and needle in files[path]:
                why += f"{path} still has {needle!r}; "
    for path in case.get("deleted", []):
        if path in files and files[path] not in (None, ""):
            why += f"{path} not deleted; "
    extra = set(files) - set(case["files"]) - set(case["expect"])
    if extra:
        why += f"extra files {sorted(extra)}; "
    return why


def test_live_harness_cases():
    api_key = os.environ.get("GPTDIFF_LLM_API_KEY")
    if not api_key:
        pytest.fail("LIVE_LLM=1 requires GPTDIFF_LLM_API_KEY")
    model = (
        os.environ.get("LIVE_LLM_MODEL")
        or os.environ.get("GPTDIFF_SMARTAPPLY_MODEL")
        or os.environ.get("GPTDIFF_MODEL")
    )

    def go(case):
        kwargs = {"api_key": api_key}
        if model:
            kwargs["model"] = model
        try:
            files = smartapply(case["diff"], dict(case["files"]), **kwargs)
        except Exception as exc:
            message = str(exc).replace(api_key, "***")[:200]
            return case["id"], f"ERR {message}"
        return case["id"], _grade(case, files)

    # Two at a time, matching the harness that surfaced parallel 402s.
    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for case_id, why in pool.map(go, CASES):
            if why:
                failures.append(f"{case_id}: {why}")
    assert not failures, "live smartapply failures:\n" + "\n".join(failures)
