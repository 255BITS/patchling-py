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
    assert parse_relevance_answer("yes for the helper, no for the tests")[0] is None
    assert parse_relevance_answer("")[0] is None
    assert parse_relevance_answer("nope")[0] is None


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
    assert by_path["ambiguous.py"].error is None
    assert "ambiguous" in by_path["ambiguous.py"].reason
    assert by_path["broken.py"].decision == "yes"
    assert "connection reset" in by_path["broken.py"].error
    assert "connection reset" in by_path["broken.py"].reason
    assert result.errors == [by_path["broken.py"]]
    summary = format_filter_summary(result)
    assert "connection reset" in summary
    assert "included by default" in summary


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


def test_duplicate_contents_share_one_request():
    calls = {"n": 0}

    def fake_call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
        calls["n"] += 1
        user = messages[-1]["content"]
        assert "a.py" in user and "b.py" in user
        return _Response("yes", prompt_tokens=4, completion_tokens=1, total_tokens=5)

    result = filter_relevant(
        {"a.py": "SAME", "b.py": "SAME"},
        "task",
        model="m",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
    )
    assert calls["n"] == 1
    assert result.decisions[0].cached is False
    assert result.decisions[1].cached is True
    assert result.billed_tokens == 5
    assert set(result.files) == {"a.py", "b.py"}


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
