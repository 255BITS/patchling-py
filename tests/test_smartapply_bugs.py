"""Deterministic reproductions of smartapply bugs found against NanoGPT.

The live harness in tests/fixtures/live_cases.json is the same suite. These
tests mock the LLM so they never make a network call. The mock returns the
broken output the live models produced; smartapply must not accept it.
"""
import json
from pathlib import Path

import openai
import pytest

from patchling.applydiff import llm_output_respects_diff
from patchling.core import SmartApplyError, sized_max_tokens, smartapply

CASES_PATH = Path(__file__).parent / "fixtures" / "live_cases.json"
CASES = json.loads(CASES_PATH.read_text())


def _grade(case, files):
    """Same checks as the live harness grader."""
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


def _drop_first_line(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=None):
    """The live failure mode: each file comes back without its first line."""
    _drop_first_line.calls.append(file_path)
    body = original_content.splitlines()
    return "\n".join(body[1:])


def _leak_context(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=None):
    """Wrong-context failure: copy the diff's bad context and drop other lines."""
    _leak_context.calls.append(file_path)
    return "TIMEOUT=30\nRETRIES = 5\nDEBUG = false\n"


def test_harness_cases_do_not_need_the_llm(monkeypatch):
    """Clean, sloppy, multi-file, create, and delete cases apply with no model."""
    _drop_first_line.calls = []
    monkeypatch.setattr("patchling.core.call_llm_for_apply", _drop_first_line)
    for case in CASES:
        original = dict(case["files"])
        updated = smartapply(case["diff"], original)
        why = _grade(case, updated)
        assert not why, f"{case['id']}: {why}\n{updated}"
        assert original == case["files"], f"{case['id']} mutated the input"
    assert _drop_first_line.calls == []


def test_clean_multifile_keeps_first_lines_when_llm_drops_them(monkeypatch):
    """Bug: a clean multi-file diff lost the first line of every file."""
    case = next(c for c in CASES if c["id"] == "multi_file")
    _drop_first_line.calls = []
    monkeypatch.setattr("patchling.core.call_llm_for_apply", _drop_first_line)
    updated = smartapply(case["diff"], dict(case["files"]))
    assert updated["x.txt"].startswith("alpha\n")
    assert updated["y.txt"].startswith("one\n")
    assert "BETA" in updated["x.txt"]
    assert "TWO" in updated["y.txt"]
    assert _drop_first_line.calls == []


def test_wrong_context_does_not_leak_or_drop_lines(monkeypatch):
    """Bug: bad context was written into the file and unrelated lines deleted."""
    case = next(c for c in CASES if c["id"] == "sloppy_wrong_context")
    _leak_context.calls = []
    monkeypatch.setattr("patchling.core.call_llm_for_apply", _leak_context)
    updated = smartapply(case["diff"], dict(case["files"]))
    text = updated["cfg.py"]
    assert "RETRIES = 5" in text
    assert "TIMEOUT = 30" in text
    assert "DEBUG = False" in text
    assert "NAME = 'svc'" in text
    assert "TIMEOUT=30" not in text
    assert "DEBUG = false" not in text
    assert "RETRIES = 3" not in text
    assert _leak_context.calls == []


def test_llm_context_leak_is_rejected(monkeypatch, capsys):
    """When the model must run, a context leak is an error, not a new file."""
    case = next(c for c in CASES if c["id"] == "sloppy_wrong_context")
    monkeypatch.setattr("patchling.core.apply_patch_to_content", lambda *a, **k: None)
    monkeypatch.setattr("patchling.core.apply_changes_only", lambda *a, **k: None)
    monkeypatch.setattr("patchling.core.call_llm_for_apply", _leak_context)
    _leak_context.calls = []
    with pytest.raises(SmartApplyError, match="additions and deletions"):
        smartapply(case["diff"], dict(case["files"]))
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "Exception in thread" not in captured.err


def test_llm_output_validator_accepts_only_pm_lines():
    case = next(c for c in CASES if c["id"] == "sloppy_wrong_context")
    original = case["files"]["cfg.py"]
    diff = case["diff"]
    good = "TIMEOUT = 30\nRETRIES = 5\nDEBUG = False\nNAME = 'svc'\n"
    leaked = "TIMEOUT=30\nRETRIES = 5\nDEBUG = false\n"
    dropped = "TIMEOUT = 30\nRETRIES = 5\nDEBUG = False\n"
    assert llm_output_respects_diff(original, good, diff)
    assert not llm_output_respects_diff(original, leaked, diff)
    assert not llm_output_respects_diff(original, dropped, diff)


def _httpx():
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx
    return httpx


def _status_error(status, message):
    httpx = _httpx()
    request = httpx.Request("POST", "https://nano-gpt.com/api/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": message})
    return openai.APIStatusError(message, response=response, body={"error": message})


def _timeout_error():
    httpx = _httpx()
    request = httpx.Request("POST", "https://nano-gpt.com/api/v1/chat/completions")
    return openai.APITimeoutError(request)


# Quote mismatch: the '-' line is not in the file, so neither deterministic
# applier can claim the edit and the LLM is actually invoked.
_FUZZY = (
    "--- a/file1.py\n+++ b/file1.py\n@@ -1,2 +1,2 @@\n"
    " def func1():\n-    print(\"Old func1\")\n+    print(\"New func1\")\n"
)
_FUZZY_FILES = {"file1.py": "def func1():\n    print('Old func1')\n"}


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: _status_error(402, "Error code: 402 - insufficient balance"),
        _timeout_error,
    ],
)
def test_api_errors_propagate(monkeypatch, capsys, exc_factory):
    """Bug: timeouts and 402s were swallowed and the original file returned."""
    exc = exc_factory()

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr("patchling.core.call_llm_for_apply", boom)
    with pytest.raises(openai.APIError):
        smartapply(_FUZZY, dict(_FUZZY_FILES))
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "Exception in thread" not in captured.out
    assert "Exception in thread" not in captured.err


def test_max_tokens_sized_to_input(monkeypatch):
    """Bug: default max_tokens=30000 reserved too much balance and 402'd."""
    seen = {}

    def record(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=None):
        seen["max_tokens"] = max_tokens
        seen["file_diff"] = file_diff
        seen["original"] = original_content
        return "def func1():\n    print('New func1')\n"

    monkeypatch.setattr("patchling.core.call_llm_for_apply", record)
    smartapply(_FUZZY, dict(_FUZZY_FILES))
    assert seen["max_tokens"] == sized_max_tokens(seen["original"], seen["file_diff"])
    assert seen["max_tokens"] < 30000

    bigger = "def func1():\n    print('Old func1')\n" + ("# pad\n" * 400)
    smartapply(_FUZZY, {"file1.py": bigger})
    assert seen["max_tokens"] > sized_max_tokens(_FUZZY_FILES["file1.py"], _FUZZY)
    assert seen["max_tokens"] < 30000


def test_explicit_max_tokens_is_honored(monkeypatch):
    seen = {}

    def record(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=None):
        seen["max_tokens"] = max_tokens
        return original_content

    monkeypatch.setattr("patchling.core.call_llm_for_apply", record)
    smartapply(_FUZZY, dict(_FUZZY_FILES), max_tokens=1000)
    assert seen["max_tokens"] == 1000


def test_sized_max_tokens_grows_with_input_and_stays_under_flat_default():
    small = sized_max_tokens("a = 1\n", "--- a/a.py\n+++ b/a.py\n@@\n-a = 1\n+a = 2\n")
    large = sized_max_tokens("a = 1\n" * 2000, "--- a/a.py\n+++ b/a.py\n@@\n-a = 1\n+a = 2\n")
    assert small >= 256
    assert large > small
    assert small < 30000
    assert large != 30000
