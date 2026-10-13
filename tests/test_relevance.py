"""Mocked unit tests for the opt-in relevance filter. No network."""

import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from patchling import build_environment, filter_relevant
from patchling.core import main, parse_arguments
from patchling.relevance import (
    RelevanceCache,
    format_filter_summary,
    parse_relevance_answer,
)


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Usage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _Response:
    def __init__(self, content, prompt_tokens=11, completion_tokens=3, total_tokens=14):
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt_tokens, completion_tokens, total_tokens)


def _file_body(messages) -> str:
    user = messages[-1]["content"]
    marker = "File contents:\n"
    if marker in user:
        return user.split(marker, 1)[1]
    return user


def _answer_for(messages, content):
    body = _file_body(messages)
    if "def keep" in body or "ALPHA_TOKEN" in body:
        return _Response("yes — needed for the task")
    if "BINARY_PREVIEW" in body:
        return _Response("yes the path matches")
    return _Response(content)


def _fake_yes_no(content_when_unmatched="no — unrelated"):
    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _answer_for(messages, content_when_unmatched)

    return fake_call_llm


def test_parse_yes_no_variants():
    assert parse_relevance_answer("yes")[0] == "yes"
    assert parse_relevance_answer("Yes.")[0] == "yes"
    assert parse_relevance_answer("NO\nthis file is a changelog")[0] == "no"
    assert parse_relevance_answer("Answer: yes, the parser lives here")[0] == "yes"
    decision, cleaned = parse_relevance_answer("```\nyes\n```")
    assert decision == "yes"
    assert "yes" in cleaned


def test_parse_strips_think_tags_before_deciding():
    # The think block says no; the answer says yes. Think content must not win.
    raw = "<think>no, this file is irrelevant</think>\nyes, the function to rename is here"
    decision, cleaned = parse_relevance_answer(raw)
    assert decision == "yes"
    assert "irrelevant" not in cleaned

    raw = "<think>yes, definitely edit it</think>\nno — tests only"
    assert parse_relevance_answer(raw)[0] == "no"

    # Unclosed think tag swallows the chain of thought and leaves no answer.
    assert parse_relevance_answer("<think>no, skip this file entirely")[0] is None
    assert parse_relevance_answer("<thinking>maybe</thinking>\nyes")[0] == "yes"


def test_parse_ambiguous_when_both_or_neither():
    assert parse_relevance_answer("I'm not sure about this one")[0] is None
    # The leading token is yes. A later "no" in the reason does not flip it.
    assert parse_relevance_answer("yes for the helper, no for the tests")[0] == "yes"
    assert parse_relevance_answer("")[0] is None
    assert parse_relevance_answer("nope")[0] is None
    assert parse_relevance_answer("There is no doubt this file is required")[0] is None


def test_filter_keeps_only_yes_files_and_reports_decisions():
    files = {
        "keep.py": "def keep():\n    return 1\n",
        "drop.py": "def drop():\n    return 2\n",
    }
    result = filter_relevant(
        files,
        "Update keep()",
        model="fake-model",
        cache=False,
        api_key="test-key",
        call_llm=_fake_yes_no(),
    )
    assert list(result.files) == ["keep.py"]
    assert result.files["keep.py"] == files["keep.py"]
    assert [d.path for d in result.decisions] == ["keep.py", "drop.py"]
    assert [d.decision for d in result.decisions] == ["yes", "no"]
    assert result.model == "fake-model"
    yes = result.decisions[0]
    assert yes.raw.startswith("yes")
    assert "needed" in yes.reason
    assert yes.tokens == 14
    assert yes.prompt_tokens == 11
    assert yes.completion_tokens == 3
    assert yes.cached is False
    assert yes.skipped_reason is None
    assert yes.error is None
    payload = yes.to_dict()
    for field in ("path", "decision", "reason", "raw", "tokens", "cached", "skipped_reason"):
        assert field in payload
    assert "cache_key" not in payload


def test_ambiguous_answer_and_errors_include_the_file():
    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        user = messages[-1]["content"]
        if "ambiguous.py" in user:
            return _Response("<think>no</think>\nmaybe later")
        raise RuntimeError("connection reset")

    files = {"ambiguous.py": "x = 1\n", "broken.py": "y = 2\n"}
    result = filter_relevant(
        files,
        "Touch both",
        model="fake",
        cache=False,
        api_key="test-key",
        call_llm=fake_call_llm,
    )
    assert set(result.files) == {"ambiguous.py", "broken.py"}
    by_path = {d.path: d for d in result.decisions}
    assert by_path["ambiguous.py"].decision == "yes"
    assert by_path["ambiguous.py"].error == "malformed answer"
    assert "malformed" in by_path["ambiguous.py"].reason
    assert by_path["broken.py"].decision == "yes"
    assert "connection reset" in by_path["broken.py"].error
    assert "connection reset" in by_path["broken.py"].reason
    assert by_path["ambiguous.py"].failed_open is True
    assert by_path["broken.py"].failed_open is True
    assert result.errors == [by_path["ambiguous.py"], by_path["broken.py"]]
    assert result.failed_open == [by_path["ambiguous.py"], by_path["broken.py"]]
    assert result.to_dict()["failed_open"] == 2
    summary = format_filter_summary(result)
    assert "connection reset" in summary
    assert "included by default" in summary
    assert "failed open" in summary


def test_errors_are_not_cached():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "print('a')\n"}
    kwargs = dict(model="fake", cache=cache, api_key="test-key", call_llm=fake_call_llm, task="edit a")
    first = filter_relevant(files, **kwargs)
    second = filter_relevant(files, **kwargs)
    assert first.decisions[0].decision == "yes"
    assert first.decisions[0].error
    assert second.decisions[0].decision == "no"
    assert second.decisions[0].error is None
    assert calls["n"] == 2


def test_memory_and_disk_cache_roundtrip(tmp_path):
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes", prompt_tokens=5, completion_tokens=1, total_tokens=6)

    disk = tmp_path / "relevance-cache.json"
    files = {"a.py": "print('cached')\n"}
    first_cache = RelevanceCache(disk)
    first = filter_relevant(
        files, "ship it", model="fake", cache=first_cache, api_key="test-key", call_llm=fake_call_llm
    )
    assert first.decisions[0].cached is False
    assert calls["n"] == 1
    assert disk.exists()
    stored = json.loads(disk.read_text())
    assert stored["version"] == 1
    assert len(stored["entries"]) == 1

    second = filter_relevant(
        files, "ship it", model="fake", cache=RelevanceCache(disk), api_key="test-key", call_llm=fake_call_llm
    )
    assert calls["n"] == 1
    assert second.decisions[0].cached is True
    assert second.decisions[0].tokens == 6
    assert second.billed_tokens == 0
    assert "a.py" in second.files

    # A different task, model, or body is a different key.
    filter_relevant(files, "other task", model="fake", cache=first_cache, api_key="k", call_llm=fake_call_llm)
    filter_relevant(files, "ship it", model="other-model", cache=first_cache, api_key="k", call_llm=fake_call_llm)
    filter_relevant(
        {"a.py": "print('changed')\n"},
        "ship it",
        model="fake",
        cache=first_cache,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 4


def test_cache_path_string_and_disabled_cache(tmp_path):
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    path = str(tmp_path / "cache.json")
    files = {"a.py": "v = 1\n"}
    filter_relevant(files, "task", model="m", cache=path, api_key="k", call_llm=fake_call_llm)
    filter_relevant(files, "task", model="m", cache=path, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 1

    filter_relevant(files, "task", model="m", cache=False, api_key="k", call_llm=fake_call_llm)
    filter_relevant(files, "task", model="m", cache=False, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 3


def test_corrupt_disk_cache_is_ignored(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json")
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes")

    result = filter_relevant(
        {"a.py": "v = 1\n"},
        "task",
        model="m",
        cache=RelevanceCache(path),
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 1
    assert result.decisions[0].decision == "yes"


def test_same_batch_empty_unrelated_py_no_does_not_exclude_empty_src_target_py():
    """Identical empty files are classified per path, even with the cache off."""
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        if "unrelated.py" in user.split("File contents:", 1)[0]:
            return _Response("no", prompt_tokens=4, completion_tokens=1, total_tokens=5)
        return _Response("yes", prompt_tokens=4, completion_tokens=1, total_tokens=5)

    result = filter_relevant(
        {"unrelated.py": "", "src/target.py": ""},
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    by_path = {d.path: d for d in result.decisions}
    assert calls["n"] == 2
    assert by_path["unrelated.py"].decision == "no"
    assert by_path["src/target.py"].decision == "yes"
    assert by_path["src/target.py"].cached is False
    assert "src/target.py" in result.files
    assert "unrelated.py" not in result.files
    assert result.billed_tokens == 10


def test_cross_call_empty_unrelated_py_no_does_not_exclude_empty_src_target_py():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        if "unrelated.py" in user.split("File contents:", 1)[0]:
            return _Response("no")
        return _Response("yes")

    cache = RelevanceCache()
    first = filter_relevant(
        {"unrelated.py": ""},
        "task",
        model="m",
        cache=cache,
        api_key="k",
        call_llm=fake_call_llm,
    )
    second = filter_relevant(
        {"src/target.py": ""},
        "task",
        model="m",
        cache=cache,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert first.decisions[0].decision == "no"
    assert second.decisions[0].decision == "yes"
    assert second.decisions[0].cached is False
    assert "src/target.py" in second.files
    assert calls["n"] == 2


def test_concurrency_is_bounded_and_order_is_preserved():
    state = {"current": 0, "max": 0}
    lock = threading.Lock()

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.2)
        with lock:
            state["current"] -= 1
        path_header = messages[-1]["content"].split("File contents:", 1)[0]
        # The task text names both files, so match the path header, not the task.
        relevant = "file-1.py" in path_header or "file-9.py" in path_header
        if "file-9.py" in path_header:
            time.sleep(0.05)
        return _Response("yes" if relevant else "no", prompt_tokens=1, completion_tokens=1, total_tokens=2)

    files = {f"file-{i}.py": f"body {i}\n" for i in range(10)}
    result = filter_relevant(
        files,
        "touch file-1 and file-9",
        model="m",
        concurrency=3,
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert state["max"] <= 3
    assert state["max"] >= 2
    assert [d.path for d in result.decisions] == list(files)
    assert set(result.files) == {"file-1.py", "file-9.py"}


def test_binary_and_oversize_policies():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        assert "SECRET_TAIL" not in user
        if "contents omitted" in user:
            assert "bin.dat" in user
            return _Response("no")
        return _Response("yes — prefix is enough")

    binary = "hello\x00world"
    huge = "PREFIX " + ("x" * 80) + " SECRET_TAIL"

    excluded = filter_relevant(
        {"bin.dat": binary, "big.py": huge},
        "edit the prefix",
        model="m",
        cache=False,
        on_binary="exclude",
        on_oversize="exclude",
        max_bytes=40,
        max_tokens=None,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 0
    assert excluded.files == {}
    by_path = {d.path: d for d in excluded.decisions}
    assert by_path["bin.dat"].decision == "no"
    assert by_path["bin.dat"].skipped_reason == "binary"
    assert by_path["big.py"].decision == "no"
    assert by_path["big.py"].skipped_reason == "oversize"
    assert by_path["big.py"].tokens == 0

    included = filter_relevant(
        {"bin.dat": binary, "big.py": huge},
        "edit the prefix",
        model="m",
        cache=False,
        on_binary="include",
        on_oversize="include",
        max_bytes=40,
        max_tokens=None,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 0
    assert set(included.files) == {"bin.dat", "big.py"}
    assert included.files["big.py"] == huge
    assert {d.decision for d in included.decisions} == {"yes"}

    truncated = filter_relevant(
        {"bin.dat": b"\x00\x01BINARY_PREVIEW", "big.py": huge},
        "edit the prefix",
        model="m",
        cache=False,
        api_key="k",
        on_binary="truncate",
        on_oversize="truncate",
        max_bytes=40,
        max_tokens=None,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 2
    by_path = {d.path: d for d in truncated.decisions}
    assert by_path["big.py"].decision == "yes"
    assert by_path["big.py"].skipped_reason == "truncated"
    assert truncated.files["big.py"] == huge  # full contents still go downstream
    assert by_path["bin.dat"].decision == "no"
    assert by_path["bin.dat"].skipped_reason == "truncated"
    assert "bin.dat" not in truncated.files


def test_truncated_judgment_is_not_reused_as_a_full_read(tmp_path):
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        if "truncated" in user:
            return _Response("no")
        return _Response("yes")

    cache = RelevanceCache(tmp_path / "cache.json")
    files = {"big.py": "PREFIX " + ("x" * 50)}
    first = filter_relevant(
        files, "task", model="m", cache=cache, api_key="k", max_bytes=20, max_tokens=None,
        on_oversize="truncate", call_llm=fake_call_llm,
    )
    second = filter_relevant(
        files, "task", model="m", cache=cache, api_key="k", max_bytes=10_000, max_tokens=None,
        call_llm=fake_call_llm,
    )
    assert first.decisions[0].decision == "no"
    assert second.decisions[0].decision == "yes"
    assert second.decisions[0].cached is False
    assert calls["n"] == 2


def test_missing_api_key_raises_only_when_a_call_is_required(monkeypatch):
    monkeypatch.delenv("GPTDIFF_LLM_API_KEY", raising=False)

    def fail_call(**kwargs):
        raise AssertionError("should not be called")

    result = filter_relevant(
        {"bin.dat": "\x00"},
        "task",
        model="m",
        cache=False,
        on_binary="exclude",
        call_llm=fail_call,
    )
    assert result.files == {}

    with pytest.raises(RuntimeError, match="GPTDIFF_LLM_API_KEY"):
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            model="m",
            cache=False,
            call_llm=fail_call,
        )


def test_cached_files_do_not_need_an_api_key(monkeypatch):
    monkeypatch.delenv("GPTDIFF_LLM_API_KEY", raising=False)
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    filter_relevant(files, "task", model="m", cache=cache, api_key="k", call_llm=fake_call_llm)
    result = filter_relevant(files, "task", model="m", cache=cache, api_key=None, call_llm=fake_call_llm)
    assert calls["n"] == 1
    assert result.decisions[0].cached is True
    assert result.decisions[0].decision == "no"


def test_invalid_arguments():
    with pytest.raises(ValueError, match="concurrency"):
        filter_relevant({"a.py": "x"}, "task", concurrency=0, cache=False, api_key="k")
    with pytest.raises(ValueError, match="on_oversize"):
        filter_relevant({"a.py": "x"}, "task", on_oversize="drop", cache=False, api_key="k")
    with pytest.raises(ValueError, match="task"):
        filter_relevant({"a.py": "x"}, "  ", cache=False, api_key="k")
    with pytest.raises(TypeError):
        filter_relevant([("a.py", "x")], "task", cache=False, api_key="k")
    with pytest.raises(TypeError):
        filter_relevant({"a.py": "x"}, "task", cache=object(), api_key="k")


def test_build_environment_option_filters_and_records_decisions(monkeypatch):
    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _answer_for(messages, "no")

    monkeypatch.setattr("patchling.core.call_llm", fake_call_llm)
    files = {"keep.py": "ALPHA_TOKEN = 1\n", "drop.py": "other = 2\n"}
    decisions = []
    env = build_environment(
        files,
        filter_relevant=True,
        task="rename ALPHA_TOKEN",
        filter_model="fake",
        cache=False,
        api_key="test-key",
        decisions=decisions,
    )
    assert "File: keep.py" in env
    assert "ALPHA_TOKEN" in env
    assert "drop.py" not in env
    assert "other = 2" not in env
    assert [d.decision for d in decisions] == ["yes", "no"]

    untouched = build_environment(files)
    assert "drop.py" in untouched

    with pytest.raises(ValueError, match="task"):
        build_environment(files, filter_relevant=True)


def test_cli_filter_flags_parse():
    old = sys.argv
    try:
        sys.argv = [
            "patchling",
            "rename the helper",
            "--filter-relevant",
            "--filter-model",
            "cheap-model",
            "--filter-concurrency",
            "4",
            "--filter-cache",
            "cache.json",
            "--filter-max-bytes",
            "1000",
            "--filter-max-tokens",
            "0",
            "--filter-oversize",
            "exclude",
            "--filter-binary",
            "include",
            "--filter-decisions",
            "decisions.json",
        ]
        args = parse_arguments()
    finally:
        sys.argv = old
    assert args.filter_relevant is True
    assert args.filter_model == "cheap-model"
    assert args.filter_concurrency == 4
    assert args.filter_cache == "cache.json"
    assert args.filter_max_bytes == 1000
    assert args.filter_max_tokens == 0
    assert args.filter_oversize == "exclude"
    assert args.filter_binary == "include"
    assert args.filter_decisions == "decisions.json"


def test_cli_filter_narrows_the_prompt(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "keep.py").write_text("def keep():\n    return 1\n")
    (project / "drop.py").write_text("def drop():\n    return 2\n")
    decisions_path = project / "decisions.json"
    monkeypatch.chdir(project)
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "test-key")
    monkeypatch.setattr("patchling.core.shutil.which", lambda _name: None)

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _answer_for(messages, "no — unrelated")

    monkeypatch.setattr("patchling.core.call_llm", fake_call_llm)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "patchling",
            "update keep",
            "--filter-relevant",
            "--filter-model",
            "fake",
            "--nobeep",
            "--filter-decisions",
            str(decisions_path),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    prompt = (project / "prompt.txt").read_text()
    assert "def keep" in prompt
    assert "def drop" not in prompt
    saved = json.loads(decisions_path.read_text())
    by_path = {d["path"]: d for d in saved["decisions"]}
    assert by_path["keep.py"]["decision"] == "yes"
    assert by_path["drop.py"]["decision"] == "no"
    assert saved["files"] == ["keep.py"]


def test_cli_zero_disables_size_limits(monkeypatch):
    from patchling.core import _apply_relevance_filter

    seen = {}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        seen["user"] = messages[-1]["content"]
        return _Response("yes")

    monkeypatch.setattr("patchling.core.call_llm", fake_call_llm)
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "test-key")

    class Args:
        filter_model = "fake"
        filter_concurrency = 2
        filter_cache = False
        filter_oversize = "truncate"
        filter_binary = "exclude"
        filter_max_bytes = 0
        filter_max_tokens = 0
        filter_decisions = None
        verbose = False

    body = "y" * 250_000
    narrowed = _apply_relevance_filter([("big.py", body)], "read the whole file", Args())
    assert len(narrowed) == 1
    assert "truncated to fit" not in seen["user"]
    assert seen["user"].count("y") >= 250_000


def test_model_defaults_to_filter_env(monkeypatch):
    monkeypatch.setenv("GPTDIFF_FILTER_MODEL", "env-filter-model")
    seen = {}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        seen["model"] = model
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        return _Response("yes")

    monkeypatch.setenv("GPTDIFF_LLM_BASE_URL", "https://example.test/v1/")
    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        cache=False,
        api_key="from-arg",
        call_llm=fake_call_llm,
    )
    assert seen["model"] == "env-filter-model"
    assert seen["base_url"] == "https://example.test/v1/"
    assert seen["api_key"] == "from-arg"
    assert result.model == "env-filter-model"


class _StatusError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def test_every_model_gets_1024_tokens_and_low_effort():
    from patchling.relevance import output_token_budget, reasoning_effort_for

    assert output_token_budget("deepseek-reasoner", 1024) == 1024
    assert output_token_budget("xiaomi/mimo-v2.6-flash", 1024) == 1024
    assert reasoning_effort_for("deepseek-reasoner") == "low"
    assert reasoning_effort_for("xiaomi/mimo-v2.6-flash") == "low"

    seen = []

    def fake_call_llm(**kwargs):
        seen.append(kwargs)
        return _Response("yes the parser is here")

    result = filter_relevant(
        {"a.py": "def keep():\n    return 1\n"},
        "edit the parser",
        model="deepseek-reasoner",
        cache=False,
        api_key="test-key",
        call_llm=fake_call_llm,
    )
    assert seen[0]["max_tokens"] == 1024
    assert seen[0]["reasoning_effort"] == "low"
    assert result.decisions[0].decision == "yes"
    assert result.decisions[0].failed_open is False


def test_non_reasoning_default_model_uses_the_shared_budget():
    seen = []

    def fake_call_llm(**kwargs):
        seen.append(kwargs)
        return _Response("no")

    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        model="xiaomi/mimo-v2.6-flash",
        cache=False,
        api_key="test-key",
        call_llm=fake_call_llm,
    )
    assert seen[0]["max_tokens"] == 1024
    assert seen[0]["reasoning_effort"] == "low"
    assert result.decisions[0].decision == "no"


def test_rejected_reasoning_effort_is_retried_without_it():
    seen = []

    def fake_call_llm(**kwargs):
        seen.append(kwargs.get("reasoning_effort"))
        if kwargs.get("reasoning_effort"):
            raise _StatusError("400 invalid reasoning_effort: unsupported", 400)
        return _Response("no — unrelated")

    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        model="deepseek-reasoner",
        cache=False,
        api_key="test-key",
        call_llm=fake_call_llm,
    )
    assert seen == ["low", None]
    assert result.decisions[0].decision == "no"
    assert result.decisions[0].error is None
    assert result.failed_open == []


def test_empty_answer_is_an_error_and_is_not_cached():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response("<think>the whole budget went here</think>   ")
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "print('a')\n"}
    kwargs = dict(model="deepseek-reasoner", cache=cache, api_key="test-key", call_llm=fake_call_llm, task="edit a")
    first = filter_relevant(files, **kwargs)
    assert first.decisions[0].decision == "yes"
    assert first.decisions[0].failed_open is True
    assert first.decisions[0].error == "empty answer"
    assert "a.py" in first.files
    assert len(first.failed_open) == 1
    assert "failed open" in format_filter_summary(first)
    assert cache.get(first.decisions[0].cache_key) is None

    second = filter_relevant(files, **kwargs)
    assert calls["n"] == 2
    assert second.decisions[0].decision == "no"
    assert second.decisions[0].failed_open is False
    assert second.decisions[0].cached is False
    third = filter_relevant(files, **kwargs)
    assert third.decisions[0].cached is True
    assert calls["n"] == 2


def test_ambiguous_answers_are_not_cached():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response("maybe later")
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    first = filter_relevant(files, "task", model="fake", cache=cache, api_key="k", call_llm=fake_call_llm)
    second = filter_relevant(files, "task", model="fake", cache=cache, api_key="k", call_llm=fake_call_llm)
    assert first.decisions[0].decision == "yes"
    assert first.decisions[0].error == "malformed answer"
    assert first.decisions[0].failed_open is True
    assert second.decisions[0].decision == "no"
    assert calls["n"] == 2


def test_cache_key_includes_output_limit_prompt_version_effort_and_path():
    from patchling.relevance import PROMPT_VERSION

    base = dict(model="m", task="task", content="body", path="a.py")
    low = RelevanceCache.make_key(**base, max_output_tokens=256, prompt_version=PROMPT_VERSION, effort="low")
    high = RelevanceCache.make_key(**base, max_output_tokens=1024, prompt_version=PROMPT_VERSION, effort="low")
    other_prompt = RelevanceCache.make_key(**base, max_output_tokens=256, prompt_version="9", effort="low")
    other_effort = RelevanceCache.make_key(**base, max_output_tokens=256, prompt_version=PROMPT_VERSION, effort="none")
    other_path = RelevanceCache.make_key(
        model="m", task="task", content="body", path="b.py",
        max_output_tokens=256, prompt_version=PROMPT_VERSION, effort="low",
    )
    other_body = RelevanceCache.make_key(
        model="m", task="task", content="truncated", path="a.py",
        max_output_tokens=256, prompt_version=PROMPT_VERSION, effort="low",
    )
    assert len({low, high, other_prompt, other_effort, other_path, other_body}) == 6
    assert PROMPT_VERSION == "3"

    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    filter_relevant(files, "task", model="m", cache=cache, api_key="k", max_output_tokens=256, call_llm=fake_call_llm)
    filter_relevant(files, "task", model="m", cache=cache, api_key="k", max_output_tokens=128, call_llm=fake_call_llm)
    filter_relevant(files, "task", model="m", cache=cache, api_key="k", max_output_tokens=256, call_llm=fake_call_llm)
    assert calls["n"] == 2


def test_transient_errors_retry_with_backoff_then_succeed():
    calls = {"n": 0}
    sleeps = []

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _StatusError("503 billing reservation", 503)
        return _Response("yes")

    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        model="fake",
        cache=False,
        api_key="k",
        max_attempts=3,
        retry_backoff=0.5,
        sleep=sleeps.append,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]
    assert result.decisions[0].decision == "yes"
    assert result.decisions[0].error is None
    assert result.failed_open == []


def test_exhausted_timeouts_fail_open_and_are_not_cached():
    calls = {"n": 0}
    sleeps = []

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        raise TimeoutError("request timed out")

    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    kwargs = dict(
        model="fake",
        cache=cache,
        api_key="k",
        max_attempts=3,
        retry_backoff=0.5,
        sleep=sleeps.append,
        call_llm=fake_call_llm,
        task="task",
    )
    result = filter_relevant(files, **kwargs)
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]
    assert result.decisions[0].decision == "yes"
    assert result.decisions[0].failed_open is True
    assert "timed out" in result.decisions[0].error
    assert "1 failed open" in format_filter_summary(result)
    assert cache.get(result.decisions[0].cache_key) is None

    def succeed(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    kwargs["call_llm"] = succeed
    second = filter_relevant(files, **kwargs)
    assert second.decisions[0].decision == "no"
    assert calls["n"] == 4


def test_non_transient_errors_are_not_retried():
    calls = {"n": 0}
    sleeps = []

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        raise RuntimeError("connection reset")

    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        model="fake",
        cache=False,
        api_key="k",
        sleep=sleeps.append,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 1
    assert sleeps == []
    assert result.decisions[0].failed_open is True


def test_lockfiles_and_generated_files_are_skipped_by_default():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes")

    files = {
        "uv.lock": "version = 1\n",
        "package-lock.json": "{}\n",
        "yarn.lock": "# yarn\n",
        "poetry.lock": "# poetry\n",
        "src/app.min.js": "function(){}\n",
        "src/app.min.mjs": "export default 1\n",
        "src/app.py": "def keep():\n    return 1\n",
    }
    result = filter_relevant(files, "edit the app", model="fake", cache=False, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 1
    by_path = {d.path: d for d in result.decisions}
    for path in ("uv.lock", "package-lock.json", "yarn.lock", "poetry.lock", "src/app.min.js", "src/app.min.mjs"):
        assert by_path[path].decision == "no"
        assert by_path[path].skipped_reason == "ignored"
        assert path not in result.files
    assert "src/app.py" in result.files


def test_skip_patterns_are_configurable():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes")

    files = {"uv.lock": "lock\n", "notes.tmp": "temp\n", "a.py": "x = 1\n"}
    sent = filter_relevant(
        files, "task", model="fake", cache=False, api_key="k", skip_patterns=(), call_llm=fake_call_llm
    )
    assert calls["n"] == 3
    assert set(sent.files) == set(files)

    calls["n"] = 0
    custom = filter_relevant(
        files,
        "task",
        model="fake",
        cache=False,
        api_key="k",
        skip_patterns=["*.tmp"],
        call_llm=fake_call_llm,
    )
    by_path = {d.path: d for d in custom.decisions}
    assert by_path["notes.tmp"].skipped_reason == "ignored"
    assert by_path["notes.tmp"].decision == "no"
    assert by_path["uv.lock"].decision == "yes"
    assert calls["n"] == 2


def test_default_filter_model_is_mimo_flash(monkeypatch):
    monkeypatch.delenv("GPTDIFF_FILTER_MODEL", raising=False)
    seen = {}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        seen["model"] = model
        return _Response("yes")

    result = filter_relevant(
        {"a.py": "x = 1\n"},
        "task",
        cache=False,
        api_key="from-arg",
        call_llm=fake_call_llm,
    )
    assert seen["model"] == "xiaomi/mimo-v2.6-flash"
    assert result.model == "xiaomi/mimo-v2.6-flash"


def test_cli_skip_generated_flags(monkeypatch):
    from patchling.core import _apply_relevance_filter

    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes")

    monkeypatch.setattr("patchling.core.call_llm", fake_call_llm)
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "test-key")

    class Args:
        filter_model = "fake"
        filter_concurrency = 2
        filter_cache = False
        filter_oversize = "truncate"
        filter_binary = "exclude"
        filter_max_bytes = None
        filter_max_tokens = None
        filter_decisions = None
        verbose = False
        filter_skip_pattern = None
        filter_no_skip_generated = False

    files = [("uv.lock", "lock\n"), ("a.py", "x = 1\n")]
    narrowed = _apply_relevance_filter(files, "task", Args())
    assert calls["n"] == 1
    assert [path for path, _content in narrowed] == ["a.py"]

    Args.filter_no_skip_generated = True
    narrowed = _apply_relevance_filter(files, "task", Args())
    assert calls["n"] == 3
    assert [path for path, _content in narrowed] == ["uv.lock", "a.py"]


def test_parser_quoted_answer():
    assert parse_relevance_answer('"yes"')[0] == "yes"
    assert parse_relevance_answer("'no'")[0] == "no"
    assert parse_relevance_answer("**yes**")[0] == "yes"
    assert parse_relevance_answer("`no`")[0] == "no"


def test_parser_negated_there_is_no_doubt():
    decision, _cleaned = parse_relevance_answer("There is no doubt this file is required")
    assert decision is None


def test_parser_explanatory_wording():
    decision, _cleaned = parse_relevance_answer("This file is unrelated to the task")
    assert decision is None


def test_fallback_failed_open_counts_each_path():
    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _Response("maybe", prompt_tokens=4, completion_tokens=1, total_tokens=5)

    result = filter_relevant(
        {"a.py": "SAME", "b.py": "SAME"},
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert [d.failed_open for d in result.decisions] == [True, True]
    assert [d.cached for d in result.decisions] == [False, False]
    assert result.to_dict()["failed_open"] == 2
    assert len(result.errors) == 2
    assert result.billed_tokens == 10
    assert "2 failed open" in format_filter_summary(result)


def test_cache_read_error_is_a_miss_and_calls_the_model():
    calls = {"n": 0}

    class Boom(RelevanceCache):
        def get(self, key):
            raise OSError("read failed")

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    result = filter_relevant(
        {"a.py": "x\n", "b.py": "y\n"},
        "task",
        model="m",
        cache=Boom(),
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 2
    assert result.failed_open == []
    assert result.files == {}
    assert [d.decision for d in result.decisions] == ["no", "no"]


def test_cache_write_error_does_not_abort():
    class FlushBoom(RelevanceCache):
        def flush(self):
            raise OSError("write failed")

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _Response("no")

    result = filter_relevant(
        {"a.py": "x\n"},
        "task",
        model="m",
        cache=FlushBoom(),
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert result.decisions[0].decision == "no"
    assert result.decisions[0].failed_open is False
    assert result.files == {}


def test_skip_patterns_are_case_insensitive():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("yes")

    files = {
        "UV.LOCK": "lock\n",
        "dist/app.js": "x\n",
        "pkg/__pycache__/m.pyc": "x\n",
        "src/App.MIN.JS": "function(){}\n",
        "src/App.MIN.MJS": "export default 1\n",
        "src/app.py": "def keep():\n    return 1\n",
    }
    result = filter_relevant(files, "edit the app", model="fake", cache=False, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 1
    assert list(result.files) == ["src/app.py"]
    skipped = {d.path for d in result.decisions if d.skipped_reason == "ignored"}
    assert skipped == {"UV.LOCK", "dist/app.js", "pkg/__pycache__/m.pyc", "src/App.MIN.JS", "src/App.MIN.MJS"}


def _parity_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "parity"


def test_parity_truncation_fixtures():
    from patchling.relevance import truncate_utf8

    payload = json.loads((_parity_dir() / "truncation.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        got = truncate_utf8(case["text"], case["max_bytes"])
        assert got == case["expected"], case["id"]
        assert "\ufffd" not in got


def test_parity_binary_cut_fixtures():
    from patchling.relevance import is_binary_content

    payload = json.loads((_parity_dir() / "binary.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        if "hex" in case:
            sample = bytes.fromhex(case["hex"])
        else:
            tail = bytes.fromhex(case["tail_hex"])
            fill = case.get("fill", "a").encode("ascii")
            sample = fill * (case["length"] - len(tail)) + tail
        assert is_binary_content(sample) is case["expect_binary"], case["id"]


def test_parity_parser_fixtures():
    payload = json.loads((_parity_dir() / "parser.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        decision, _cleaned = parse_relevance_answer(case["raw"])
        assert decision == case["decision"], case["id"]


def test_token_estimate_is_ceil_code_points_over_4():
    from patchling.relevance import count_tokens, _truncate_text

    assert count_tokens("") == 0
    assert count_tokens("abcd") == 1
    assert count_tokens("abcde") == 2
    assert count_tokens("a" * 8) == 2
    assert count_tokens("abc😀def") == 2
    assert count_tokens("中文测试") == 1
    assert count_tokens("e\u0301") == 1
    assert _truncate_text("abc😀def", None, 1, None) == "abc😀"
    assert _truncate_text("中文测试啊", None, 1, None) == "中文测试"
    # Combining mark is its own code point, so the cap can split a grapheme.
    marked = "a" + "\u0301" * 5
    assert _truncate_text(marked, None, 1, None) == "a" + "\u0301" * 3
    assert _truncate_text("éX", 1, None, None) == ""
    assert _truncate_text("€X", 2, None, None) == ""
    assert _truncate_text("😀X", 3, None, None) == ""


def test_parser_wrappers_and_terminators():
    assert parse_relevance_answer("__yes__")[0] == "yes"
    assert parse_relevance_answer("\u201cno\u201d")[0] == "no"
    assert parse_relevance_answer("\u2018yes\u2019")[0] == "yes"
    assert parse_relevance_answer("yes)")[0] == "yes"
    assert parse_relevance_answer("no!")[0] == "no"
    assert parse_relevance_answer("yes?")[0] is None
    assert parse_relevance_answer("```python\nyes\n```")[0] == "yes"
    assert parse_relevance_answer("**yes**, because it is")[0] == "yes"


def test_parser_quoted_decision_followed_by_reason():
    assert parse_relevance_answer("**no** unrelated")[0] == "no"
    assert parse_relevance_answer('"no" unrelated')[0] == "no"
    assert parse_relevance_answer("\u201cno\u201d — unrelated")[0] == "no"
    assert parse_relevance_answer("**yes** the helper is here")[0] == "yes"


def test_full_and_truncated_abc_do_not_share_a_key():
    full = RelevanceCache.make_key(
        model="m", task="task", content="abc", path="a.py", mode="full", note=""
    )
    note = (
        "Note: this file was truncated to fit the relevance check "
        "(6 bytes, 2 tokens). Judge from the portion shown."
    )
    truncated = RelevanceCache.make_key(
        model="m", task="task", content="abc", path="a.py", mode="truncated", note=note
    )
    assert full != truncated

    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        return _Response("no")

    cache = RelevanceCache()
    kwargs = dict(model="m", cache=cache, api_key="k", call_llm=fake_call_llm, max_tokens=None)
    filter_relevant({"a.py": "abc"}, "task", **kwargs)
    filter_relevant(
        {"a.py": "abcdef"}, "task", max_bytes=3, on_oversize="truncate", **kwargs
    )
    assert calls["n"] == 2


def test_rejected_effort_is_cached_under_the_empty_key():
    from patchling.relevance import PROMPT_VERSION

    seen = []

    def fake_call_llm(**kwargs):
        seen.append(kwargs.get("reasoning_effort"))
        if kwargs.get("reasoning_effort"):
            raise _StatusError("400 invalid reasoning_effort: unsupported", 400)
        return _Response("no")

    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    kwargs = dict(model="m", cache=cache, api_key="k", call_llm=fake_call_llm)
    first = filter_relevant(files, "task", **kwargs)
    second = filter_relevant(files, "task", **kwargs)
    assert seen == ["low", None]
    assert first.decisions[0].cached is False
    assert second.decisions[0].cached is True
    assert second.decisions[0].decision == "no"
    common = dict(
        model="m",
        task="task",
        content="x = 1\n",
        path="a.py",
        max_output_tokens=1024,
        prompt_version=PROMPT_VERSION,
        mode="full",
        note="",
    )
    low = RelevanceCache.make_key(**common, effort="low")
    omitted = RelevanceCache.make_key(**common, effort="")
    assert cache.get(low) is None
    assert cache.get(omitted)["decision"] == "no"


def test_valid_utf8_bytes_are_decoded_and_do_not_share_a_path_key():
    bodies = []

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        bodies.append(_file_body(messages))
        return _Response("yes" if len(bodies) == 1 else "no")

    cache = RelevanceCache()
    kwargs = dict(model="m", cache=cache, api_key="k", call_llm=fake_call_llm)
    first = filter_relevant({"a.py": "alpha".encode()}, "task", **kwargs)
    second = filter_relevant({"a.py": "beta".encode()}, "task", **kwargs)
    assert first.files["a.py"] == "alpha"
    assert first.files["a.py"] != ""
    assert "alpha" in bodies[0]
    assert "beta" in bodies[1]
    assert second.decisions[0].cached is False
    assert second.decisions[0].decision == "no"
    assert second.files == {}

    invalid = filter_relevant(
        {"a.py": b"\xff\xfe"},
        "task",
        model="m",
        cache=False,
        api_key="k",
        on_binary="exclude",
        call_llm=fake_call_llm,
    )
    assert invalid.decisions[0].skipped_reason == "binary"
    assert invalid.files == {}
    assert len(bodies) == 2


def test_invalid_utf8_after_probe_window_is_binary_and_include_keeps_bytes():
    from patchling.relevance import _prepare_content, is_binary_content

    payload = b"a" * 8192 + b"\xff" + b"TAIL"
    assert is_binary_content(payload) is True
    assert _prepare_content(payload) == (True, "")

    # A character split by the 8192 window is still text when the whole file is valid.
    split = b"a" * 8191 + "é".encode("utf-8") + b"TAIL"
    assert len(split) > 8192
    binary, text = _prepare_content(split)
    assert binary is False
    assert text.endswith("éTAIL")
    assert "\ufffd" not in text

    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        assert "TAIL" not in user
        return _Response("yes")

    excluded = filter_relevant(
        {"a.bin": payload},
        "task",
        model="m",
        cache=False,
        api_key="k",
        on_binary="exclude",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 0
    assert excluded.files == {}
    assert excluded.decisions[0].decision == "no"
    assert excluded.decisions[0].skipped_reason == "binary"

    included = filter_relevant(
        {"a.bin": payload},
        "task",
        model="m",
        cache=False,
        api_key="k",
        on_binary="include",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 0
    assert included.decisions[0].decision == "yes"
    assert included.files["a.bin"] is payload
    assert included.files["a.bin"].endswith(b"\xffTAIL")


def test_truncated_utf8_tail_keeps_the_valid_prefix():
    from patchling.relevance import _decode_utf8_text, _prepare_content, is_binary_content

    short = b"a\xc3"
    assert is_binary_content(short) is False
    assert _decode_utf8_text(short) == "a"
    assert _prepare_content(short) == (False, "a")
    assert _prepare_content(b"hello\xe2\x82") == (False, "hello")
    assert _prepare_content(b"a\xf0\x9f\x98") == (False, "a")
    # A lone incomplete sequence decodes to empty text and stays binary.
    assert is_binary_content(b"\xc3") is True
    assert _prepare_content(b"\xc3") == (True, "")
    # A bad continuation is not a truncated tail.
    assert is_binary_content(b"a\xc3X") is True
    assert _decode_utf8_text(b"a\xc3X") is None

    long = b"a" * 8192 + b"\xc3"
    assert len(long) > 8192
    assert is_binary_content(long) is False
    binary, text = _prepare_content(long)
    assert binary is False
    assert text == "a" * 8192
    assert "\ufffd" not in text

    bodies = []

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        bodies.append(messages[-1]["content"])
        return _Response("yes")

    result = filter_relevant(
        {"a.py": short},
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert result.decisions[0].skipped_reason is None
    assert result.files["a.py"] == "a"
    assert "File contents:\na\n\nIs this file relevant" in bodies[0]
    assert "\ufffd" not in bodies[0]


def test_ignored_binary_is_not_decoded(monkeypatch):
    import patchling.relevance as relevance

    payload = bytes([0x61, 0xC3, 0xFF, 0x00])
    decoded = []

    def _spy(name, fn):
        def wrapped(content, *args, **kwargs):
            raw = content
            if isinstance(raw, (bytes, bytearray)) and bytes(raw) == payload:
                decoded.append(name)
            return fn(content, *args, **kwargs)
        return wrapped

    for name in (
        "_prepare_content",
        "is_binary_content",
        "_decode_utf8_text",
        "_utf8_text_or_none",
        "_coerce_text",
    ):
        monkeypatch.setattr(relevance, name, _spy(name, getattr(relevance, name)))

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        user = messages[-1]["content"]
        assert "dist/app.min.js" not in user
        return _Response("yes")

    result = filter_relevant(
        {
            "dist/app.min.js": payload,
            "src/keep.js": "export const n = 1;\n",
        },
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    ignored = next(item for item in result.decisions if item.path == "dist/app.min.js")
    assert ignored.decision == "no"
    assert ignored.skipped_reason == "ignored"
    assert "dist/app.min.js" not in result.files
    assert decoded == []
    assert result.files["src/keep.js"] == "export const n = 1;\n"


def test_leading_bom_is_preserved_in_decoded_text():
    from patchling.relevance import PROMPT_VERSION, _prepare_content, _truncate_text

    raw = b"\xef\xbb\xbfhello"
    binary, text = _prepare_content(raw)
    assert binary is False
    assert text == "\ufeffhello"
    assert text.startswith("\ufeff")
    assert _truncate_text(text, None, 1, None).startswith("\ufeff")

    with_bom = RelevanceCache.make_key(
        model="m",
        task="task",
        content=text,
        path="a.py",
        max_output_tokens=1024,
        prompt_version=PROMPT_VERSION,
        effort="low",
        mode="full",
        note="",
    )
    without_bom = RelevanceCache.make_key(
        model="m",
        task="task",
        content="hello",
        path="a.py",
        max_output_tokens=1024,
        prompt_version=PROMPT_VERSION,
        effort="low",
        mode="full",
        note="",
    )
    assert with_bom != without_bom

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        assert "\ufeffhello" in messages[-1]["content"]
        return _Response("yes")

    result = filter_relevant(
        {"a.py": raw},
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert result.files["a.py"] == "\ufeffhello"


def test_non_string_message_content_fails_open_uncached():
    class Message:
        content = ["no"]

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]
        usage = None

    payloads = [
        {"choices": [{"message": {"content": ["no"]}}]},
        Response(),
    ]
    for payload in payloads:
        calls = {"n": 0}

        def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None, payload=payload):
            calls["n"] += 1
            return payload

        cache = RelevanceCache()
        result = filter_relevant(
            {"a.py": "x\n"},
            "task",
            model="m",
            cache=cache,
            api_key="k",
            call_llm=fake_call_llm,
        )
        decision = result.decisions[0]
        assert decision.failed_open is True
        assert decision.decision == "yes"
        assert decision.cached is False
        assert "malformed response" in (decision.error or "")
        assert cache.get(decision.cache_key) is None if decision.cache_key else True
        again = filter_relevant(
            {"a.py": "x\n"},
            "task",
            model="m",
            cache=cache,
            api_key="k",
            call_llm=fake_call_llm,
        )
        assert again.decisions[0].cached is False
        assert calls["n"] == 2


def test_default_wrapper_caches_empty_effort_when_legacy_sdk_rejects_it(monkeypatch):
    """The real call_llm retries a legacy SDK; the cache key uses effort ""."""
    from patchling.relevance import PROMPT_VERSION

    calls = []

    class Message:
        content = "no, unrelated"

    class Choice:
        message = Message()

    class Usage:
        prompt_tokens = 3
        completion_tokens = 2
        total_tokens = 5

    class Response:
        choices = [Choice()]
        usage = Usage()

    class Completions:
        def create(self, **kwargs):
            calls.append("reasoning_effort" in kwargs)
            if "reasoning_effort" in kwargs:
                raise TypeError("unexpected keyword argument 'reasoning_effort'")
            return Response()

    class Chat:
        completions = Completions()

    class Client:
        def __init__(self, **kwargs):
            self.chat = Chat()

    monkeypatch.setattr("patchling.core.OpenAI", Client)
    cache = RelevanceCache()
    files = {"a.py": "x = 1\n"}
    first = filter_relevant(files, "task", model="m", cache=cache, api_key="k")
    second = filter_relevant(files, "task", model="m", cache=cache, api_key="k")
    assert calls == [True, False]
    assert first.decisions[0].cached is False
    assert first.decisions[0].decision == "no"
    assert second.decisions[0].cached is True
    assert second.decisions[0].decision == "no"
    common = dict(
        model="m",
        task="task",
        content="x = 1\n",
        path="a.py",
        max_output_tokens=1024,
        prompt_version=PROMPT_VERSION,
        mode="full",
        note="",
    )
    low = RelevanceCache.make_key(**common, effort="low")
    omitted = RelevanceCache.make_key(**common, effort="")
    assert cache.get(low) is None
    assert cache.get(omitted)["decision"] == "no"


def test_envelope_errors_fail_open_and_are_not_cached():
    payloads = [
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        "this is not json",
        None,
        {"choices": [{"message": {"content": ["no"]}}]},
    ]
    for payload in payloads:
        calls = {"n": 0}

        def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None, payload=payload):
            calls["n"] += 1
            return payload

        cache = RelevanceCache()
        result = filter_relevant(
            {"a.py": "x\n"},
            "task",
            model="m",
            cache=cache,
            api_key="k",
            call_llm=fake_call_llm,
        )
        assert calls["n"] == 1, payload
        decision = result.decisions[0]
        assert decision.failed_open is True, payload
        assert decision.cached is False
        assert decision.decision == "yes"
        assert "a.py" in result.files
        assert "malformed response" in (decision.error or "")
        again = filter_relevant(
            {"a.py": "x\n"},
            "task",
            model="m",
            cache=cache,
            api_key="k",
            call_llm=fake_call_llm,
        )
        assert again.decisions[0].cached is False
        assert calls["n"] == 2


def test_call_llm_ordinary_call_is_made_once(monkeypatch):
    from patchling.core import call_llm

    calls = {"n": 0}

    class Completions:
        def create(self, **kwargs):
            calls["n"] += 1
            assert "reasoning_effort" not in kwargs
            return {"ok": True}

    class Chat:
        completions = Completions()

    class Client:
        def __init__(self, **kwargs):
            self.chat = Chat()

    monkeypatch.setattr("patchling.core.OpenAI", Client)
    assert call_llm("k", "https://example.test/v1", "m", [], 16, 0.0) == {"ok": True}
    assert calls["n"] == 1


def test_call_llm_generic_type_error_is_not_retried(monkeypatch):
    from patchling.core import call_llm

    calls = {"n": 0}

    class Completions:
        def create(self, **kwargs):
            calls["n"] += 1
            raise TypeError("boom")

    class Chat:
        completions = Completions()

    class Client:
        def __init__(self, **kwargs):
            self.chat = Chat()

    monkeypatch.setattr("patchling.core.OpenAI", Client)
    with pytest.raises(TypeError, match="boom"):
        call_llm("k", "https://example.test/v1", "m", [], 16, 0.0, reasoning_effort="low")
    assert calls["n"] == 1
    calls["n"] = 0
    with pytest.raises(TypeError, match="boom"):
        call_llm("k", "https://example.test/v1", "m", [], 16, 0.0)
    assert calls["n"] == 1


def test_call_llm_retries_only_unsupported_reasoning_effort(monkeypatch):
    from patchling.core import call_llm

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs.get("reasoning_effort", None))
            if "reasoning_effort" in kwargs:
                raise TypeError("unexpected keyword argument 'reasoning_effort'")
            return {"ok": True}

    class Chat:
        completions = Completions()

    class Client:
        def __init__(self, **kwargs):
            self.chat = Chat()

    monkeypatch.setattr("patchling.core.OpenAI", Client)
    assert call_llm(
        "k", "https://example.test/v1", "m", [], 16, 0.0, reasoning_effort="low"
    ) == {"ok": True}
    assert calls == ["low", None]


def test_cli_apply_uses_the_full_original_file_map(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "keep.py").write_text("KEEP_ON_DISK\n")
    (project / "drop.py").write_text("DROP_ON_DISK\n")
    monkeypatch.chdir(project)
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "test-key")

    loaded = {
        str(project / "keep.py"): "def keep():\n    return 'KEEP_MAP'\n",
        str(project / "drop.py"): "DROP_MAP\n",
    }

    def fake_load(project_dir, cwd):
        return [(path, content) for path, content in loaded.items()]

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        return _answer_for(messages, "no — unrelated")

    captured = {}

    def fake_for_diff(system_prompt, user_prompt, files_content, model, **kwargs):
        captured["files_content"] = files_content
        diff = (
            "diff --git a/drop.py b/drop.py\n"
            "--- a/drop.py\n"
            "+++ b/drop.py\n"
            "@@ -1 +1 @@\n"
            "-DROP_MAP\n"
            "+DROP_NEW\n"
        )
        return "full", diff, 1, 1, 2

    def fake_apply(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=None):
        captured["original"] = original_content
        captured["path"] = file_path
        return "applied\n"

    monkeypatch.setattr("patchling.core.load_project_files", fake_load)
    monkeypatch.setattr("patchling.core.call_llm", fake_call_llm)
    monkeypatch.setattr("patchling.core.call_llm_for_diff", fake_for_diff)
    monkeypatch.setattr(
        "patchling.core.call_llm_for_apply_with_think_tool_available", fake_apply
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["patchling", "update keep", "--filter-relevant", "--filter-model", "fake", "--apply", "--nobeep", "--nowarn"],
    )
    main()
    assert "KEEP_MAP" in captured["files_content"]
    assert "DROP_MAP" not in captured["files_content"]
    assert captured["path"] == "drop.py"
    assert captured["original"] == "DROP_MAP\n"


# sha256 of tests/fixtures/parity/relevance.json. That file is a byte-identical
# copy of test/fixtures/parity/relevance.json on 255BITS/patchling commit
# 034aa2f45afad11253d7deee7fa4750468925f26 (branch cursor/main-green-utf8-915b).
# A mismatch fails CI.
RELEVANCE_FIXTURE_SHA256 = "c5f3bd4af43ff2a2036b8012c2ed69c70cd9b2c09281bc58e864fb0194eebe5c"


def test_vendored_relevance_fixture_matches_library():
    """Run the shared fixture against the real key, parser, truncation, and skip list."""
    from patchling.relevance import (
        DEFAULT_SKIP_PATTERNS,
        PROMPT_VERSION,
        _SYSTEM_PROMPT,
        _build_user_prompt,
        _prepare_content,
        _truncate_text,
        count_tokens,
        is_binary_content,
        path_is_skipped,
        truncate_utf8,
    )

    path = _parity_dir() / "relevance.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == RELEVANCE_FIXTURE_SHA256
    data = json.loads(raw.decode("utf-8"))

    assert str(data["promptVersion"]) == PROMPT_VERSION
    assert data["systemPrompt"] == _SYSTEM_PROMPT

    prompts = data.get("userPrompt") or {}
    if "withoutNote" in prompts:
        got = _build_user_prompt("TASK", "PATH", "BODY", None)
        expected = (
            prompts["withoutNote"]
            .replace("{task}", "TASK")
            .replace("{intent}", "TASK")
            .replace("{path}", "PATH")
            .replace("{content}", "BODY")
        )
        assert got == expected
    if "withNote" in prompts:
        got = _build_user_prompt("TASK", "PATH", "BODY", "NOTE")
        expected = (
            prompts["withNote"]
            .replace("{task}", "TASK")
            .replace("{intent}", "TASK")
            .replace("{path}", "PATH")
            .replace("{note}", "NOTE")
            .replace("{content}", "BODY")
        )
        assert got == expected

    for example in (data.get("tokenEstimate") or {}).get("examples") or []:
        assert count_tokens(example["text"]) == example["tokens"], example

    for vector in data["cacheKey"]["vectors"]:
        limit = vector.get(
            "max_output_tokens",
            vector.get("tokenLimit", vector.get("maxOutputTokens")),
        )
        if "effort_sent" in vector:
            effort = vector["effort_sent"]
        elif "effort" in vector:
            effort = vector["effort"]
        else:
            effort = ""
        got = RelevanceCache.make_key(
            model=vector["model"],
            task=vector.get("task", vector.get("intent")),
            content=vector["content"],
            path=vector["path"],
            max_output_tokens=int(limit),
            prompt_version=str(vector.get("prompt_version", vector.get("promptVersion"))),
            effort=effort,
            mode=vector.get("mode") or "full",
            note=vector.get("note") or "",
        )
        assert got == vector["sha256"], vector

    for case in data["parser"]:
        decision, _cleaned = parse_relevance_answer(case["raw"])
        assert decision == case["decision"], case

    truncation = list(data.get("truncation") or [])
    if isinstance(data.get("truncation"), dict):
        truncation = list(data["truncation"].get("cases") or [])
    truncation.extend(data.get("tokenTruncation") or [])
    for case in truncation:
        if "maxTokens" in case:
            got = _truncate_text(case["text"], case.get("maxBytes"), case["maxTokens"], None)
        else:
            got = truncate_utf8(case["text"], case["maxBytes"])
        assert got == case["expected"], case
        assert "\ufffd" not in got

    byte_cases = data.get("bytes") or {}
    if byte_cases.get("validUtf8"):
        from patchling.relevance import _prepare_content

        sample = byte_cases["validUtf8"].encode("utf-8")
        binary, text = _prepare_content(sample)
        assert binary is False
        assert text == byte_cases["validUtf8"]
        assert text != ""
    changed = byte_cases.get("changed") or []
    if len(changed) >= 2:
        from patchling.relevance import _prepare_content

        keys = []
        for text in changed:
            binary, decoded = _prepare_content(text.encode("utf-8"))
            assert binary is False
            assert decoded == text and decoded != ""
            keys.append(
                RelevanceCache.make_key(
                    model="m",
                    task="task",
                    content=decoded,
                    path="a.py",
                    mode="full",
                    note="",
                )
            )
        assert len(set(keys)) == len(changed)

    binary = data.get("binary") or {}
    for case in binary.get("cases") or []:
        if "hex" in case:
            sample = bytes.fromhex(case["hex"])
        else:
            tail = bytes(case["tail"])
            sample = b"a" * case["pad"] + tail
        expect = case.get("binary", case.get("expect_binary"))
        assert is_binary_content(sample) is expect, case
        if "text" in case:
            binary_flag, text = _prepare_content(sample)
            assert binary_flag is False, case
            assert text == case["text"], case
            assert "\ufffd" not in text

    bom = data.get("bom")
    if bom:
        from patchling.relevance import _prepare_content

        sample = bytes(bom["bytes"])
        binary, text = _prepare_content(sample)
        assert binary is False
        assert text == bom["text"]
        assert text.startswith("\ufeff")
        assert count_tokens(text) == bom["tokens"]
        assert _truncate_text(text, None, bom["maxTokens"], None) == bom["truncated"]
        assert _truncate_text(text, None, bom["maxTokens"], None).startswith("\ufeff")

    ignore = data["ignore"]
    assert list(DEFAULT_SKIP_PATTERNS) == list(ignore["patterns"])
    for example in ignore["examples"]:
        assert path_is_skipped(example["path"], ignore["patterns"]) is example["ignored"], example
