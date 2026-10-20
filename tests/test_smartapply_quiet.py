"""smartapply is quiet by default; the diff echo is verbose-only.

Library callers (and their test suites) should not get the whole diff on
stdout for every call. patchling.core.VERBOSE, set by the CLI's --verbose,
turns the echo back on. No network: the LLM step is mocked.
"""
import patchling.core as core
from patchling.core import smartapply

CLEAN_DIFF = (
    "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n"
    " def f():\n-    return 1\n+    return 2\n"
)
CLEAN_FILES = {"a.py": "def f():\n    return 1\n"}

# Quote mismatch: the '-' line is not in the file, so neither deterministic
# applier claims the edit and the (mocked) model step runs.
FUZZY_DIFF = (
    "--- a/file1.py\n+++ b/file1.py\n@@ -1,2 +1,2 @@\n"
    " def func1():\n-    print(\"Old func1\")\n+    print(\"New func1\")\n"
)
FUZZY_FILES = {"file1.py": "def func1():\n    print('Old func1')\n"}
FUZZY_RESULT = "def func1():\n    print(\"New func1\")\n"


def _mock_llm(monkeypatch):
    calls = []

    def fake_apply(path, original, patch, model, **kwargs):
        calls.append(path)
        return FUZZY_RESULT

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", fake_apply)
    return calls


def test_verbose_is_off_by_default():
    assert core.VERBOSE is False


def test_smartapply_prints_nothing_by_default(monkeypatch, capsys):
    monkeypatch.setattr(core, "VERBOSE", False)
    result = smartapply(CLEAN_DIFF, dict(CLEAN_FILES))
    assert result["a.py"] == "def f():\n    return 2\n"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_smartapply_model_path_prints_nothing_by_default(monkeypatch, capsys):
    monkeypatch.setattr(core, "VERBOSE", False)
    calls = _mock_llm(monkeypatch)
    result = smartapply(FUZZY_DIFF, dict(FUZZY_FILES), model="unit-model")
    assert calls == ["file1.py"]
    assert result["file1.py"] == FUZZY_RESULT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_smartapply_echoes_the_diff_when_verbose(monkeypatch, capsys):
    monkeypatch.setattr(core, "VERBOSE", True)
    result = smartapply(CLEAN_DIFF, dict(CLEAN_FILES))
    assert result["a.py"] == "def f():\n    return 2\n"
    out = capsys.readouterr().out
    rule = "-" * 40
    assert out == f"{rule}\nSMARTAPPLY\n{CLEAN_DIFF}\n{rule}\n"
