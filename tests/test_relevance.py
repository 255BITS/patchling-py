"""Mocked unit tests for the opt-in relevance filter. No network."""

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
        "src/app.py": "def keep():\n    return 1\n",
    }
    result = filter_relevant(files, "edit the app", model="fake", cache=False, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 1
    by_path = {d.path: d for d in result.decisions}
    for path in ("uv.lock", "package-lock.json", "yarn.lock", "poetry.lock", "src/app.min.js"):
        assert by_path[path].decision == "no"
        assert by_path[path].skipped_reason == "generated"
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
    assert by_path["notes.tmp"].skipped_reason == "generated"
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


def test_cache_read_error_fails_open_without_aborting(monkeypatch):
    monkeypatch.delenv("GPTDIFF_LLM_API_KEY", raising=False)
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
        api_key=None,
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 0
    assert len(result.failed_open) == 2
    assert set(result.files) == {"a.py", "b.py"}
    assert all("cache error" in d.error for d in result.decisions)


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
        "src/app.py": "def keep():\n    return 1\n",
    }
    result = filter_relevant(files, "edit the app", model="fake", cache=False, api_key="k", call_llm=fake_call_llm)
    assert calls["n"] == 1
    assert list(result.files) == ["src/app.py"]
    skipped = {d.path for d in result.decisions if d.skipped_reason == "generated"}
    assert skipped == {"UV.LOCK", "dist/app.js", "pkg/__pycache__/m.pyc", "src/App.MIN.JS"}


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


def test_token_estimate_is_chars_over_4():
    from patchling.relevance import count_tokens

    assert count_tokens("") == 0
    assert count_tokens("abcd") == 1
    assert count_tokens("a" * 8) == 2
