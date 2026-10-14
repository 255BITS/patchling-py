"""CamelCase option names from the JS filter are rejected with Python spellings.

No network. The injected client fails the test if ``filter_relevant`` calls it.
"""

import inspect
import re

import pytest

from patchling import filter_relevant
from patchling.relevance import CAMEL_CASE_OPTIONS

# Vendored from 255BITS/patchling src/filter.js SNAKE_CASE_OPTIONS
# at ad022037d59b6c49e16e3b2f29e83dddf4e5faf2 (JS PR #7).
JS_SNAKE_CASE_OPTIONS = {
    "api_key": "use apiKey",
    "base_url": "use baseUrl",
    "max_bytes": "use maxBytes",
    "max_tokens": "use maxTokens",
    "on_oversize": "use onOversize",
    "on_binary": "use onBinary",
    "max_output_tokens": "use maxOutputTokens",
    "skip_patterns": "use ignore (an array replaces DEFAULT_IGNORE_PATTERNS; [] or false classifies every path)",
    "max_attempts": "use retries, which counts extra attempts: retries = max_attempts - 1",
    "retry_backoff": "use retryBaseMs, in milliseconds: retryBaseMs = retry_backoff * 1000",
    "sleep": "not supported in JS; backoff uses timers. In tests pass retryBaseMs: 0 to skip the waits",
    "call_llm": "use callLlm",
    "on_decision": "use onDecision",
    "reasoning_effort": "use reasoningEffort",
    "retry_base_ms": "use retryBaseMs",
}

_USE_NAME = re.compile(r"^use ([A-Za-z_][A-Za-z0-9_]*)\b")
_ARROW_NAME = re.compile(r"->\s*([A-Za-z_][A-Za-z0-9_]*)")

# Vendored JS snake_case keys whose camelCase option has no filter_relevant parameter.
JS_ONLY_UNSUPPORTED = {
    "on_decision": "onDecision",
    "reasoning_effort": "reasoningEffort",
}


def _recommended(guidance):
    match = _USE_NAME.match(guidance)
    if match is None:
        return None
    return match.group(1)


def _python_parameter(guidance):
    """Parameter a Python guidance string tells the caller to pass.

    ``None`` means the option is not supported and names no kwarg.
    """
    if guidance.startswith("not supported"):
        return None
    match = _USE_NAME.match(guidance)
    if match is not None:
        return match.group(1)
    match = _ARROW_NAME.search(guidance)
    if match is not None:
        return match.group(1)
    return None


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("model was called")


@pytest.mark.parametrize("key", list(CAMEL_CASE_OPTIONS))
def test_each_camel_case_option_names_its_python_equivalent(key):
    with pytest.raises(TypeError) as exc_info:
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=_fail_if_called,
            **{key: "x"},
        )
    message = str(exc_info.value)
    assert message.startswith("filter_relevant options are snake_case in Python. ")
    assert f"{key}: {CAMEL_CASE_OPTIONS[key]}" in message


def test_several_camel_case_options_are_named_in_one_error():
    keys = ["onBinary", "maxBytes", "retries", "ignore", "retryBaseMs"]
    with pytest.raises(TypeError) as exc_info:
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=_fail_if_called,
            onBinary="include",
            maxBytes=10,
            retries=1,
            ignore=[],
            retryBaseMs=200,
        )
    fixes = "; ".join(f"{key}: {CAMEL_CASE_OPTIONS[key]}" for key in keys)
    assert str(exc_info.value) == f"filter_relevant options are snake_case in Python. {fixes}"


def test_camel_case_options_raise_before_any_model_call():
    calls = {"n": 0}

    def fail_if_called(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("model was called")

    with pytest.raises(TypeError, match="onBinary: use on_binary"):
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=fail_if_called,
            onBinary="include",
        )
    assert calls["n"] == 0


def test_python_and_js_option_maps_are_exact_inverses():
    """Vendored JS map stays literal. JS-only keys are unsupported.

    ``sleep`` has no camelCase spelling. ``on_decision`` and
    ``reasoning_effort`` name JS options with no ``filter_relevant``
    parameter, so the Python message must not invent a kwarg.
    ``retry_base_ms`` is a second JS spelling of ``retryBaseMs``; the
    Python parameter behind that name is ``retry_backoff``. Every other
    key is an exact inverse.
    """
    parameters = set(inspect.signature(filter_relevant).parameters)
    js_pairs = {}
    aliases = {}
    for snake, guidance in JS_SNAKE_CASE_OPTIONS.items():
        if snake in JS_ONLY_UNSUPPORTED:
            camel = JS_ONLY_UNSUPPORTED[snake]
            assert guidance == f"use {camel}"
            assert CAMEL_CASE_OPTIONS[camel] == "not supported in Python filter_relevant"
            assert _python_parameter(CAMEL_CASE_OPTIONS[camel]) is None
            assert snake not in parameters
            assert f"use {snake}" not in CAMEL_CASE_OPTIONS[camel]
            continue
        camel = _recommended(guidance)
        if camel is None:
            assert snake == "sleep"
            assert guidance.startswith("not supported")
            continue
        if camel in js_pairs:
            aliases.setdefault(camel, []).append(snake)
            continue
        js_pairs[camel] = snake

    py_pairs = {}
    for camel, guidance in CAMEL_CASE_OPTIONS.items():
        if camel in JS_ONLY_UNSUPPORTED.values():
            continue
        name = _python_parameter(guidance)
        assert name is not None
        assert name in parameters
        py_pairs[camel] = name

    assert py_pairs == js_pairs
    assert aliases == {"retryBaseMs": ["retry_base_ms"]}
    assert "retry_base_ms" not in parameters
    assert set(CAMEL_CASE_OPTIONS) == set(js_pairs) | set(JS_ONLY_UNSUPPORTED.values())
    for camel, snake in py_pairs.items():
        assert _recommended(JS_SNAKE_CASE_OPTIONS[snake]) == camel
        assert snake in CAMEL_CASE_OPTIONS[camel]

    assert CAMEL_CASE_OPTIONS["retries"] == (
        "retries=N (extra attempts) -> max_attempts=N+1 (total attempts)"
    )
    assert JS_SNAKE_CASE_OPTIONS["max_attempts"] == (
        "use retries, which counts extra attempts: retries = max_attempts - 1"
    )
    assert CAMEL_CASE_OPTIONS["retryBaseMs"] == (
        "retryBaseMs=M (milliseconds, base delay) -> "
        "retry_backoff=M/1000 (seconds, base delay)"
    )
    assert JS_SNAKE_CASE_OPTIONS["retry_backoff"] == (
        "use retryBaseMs, in milliseconds: retryBaseMs = retry_backoff * 1000"
    )
    ignore = CAMEL_CASE_OPTIONS["ignore"]
    assert ignore.startswith("use skip_patterns")
    assert "replaces DEFAULT_SKIP_PATTERNS and does not extend it" in ignore
    assert "None applies the defaults" in ignore
    assert "[] or () classifies every path" in ignore
    assert "ignore=false" in ignore
    assert "not a regex or a substring" in ignore
    assert "** crosses directories and * does not" in ignore
    assert JS_SNAKE_CASE_OPTIONS["skip_patterns"].startswith("use ignore")
    assert _recommended(JS_SNAKE_CASE_OPTIONS["retry_base_ms"]) == "retryBaseMs"


def test_camel_case_is_reported_with_an_unrelated_unknown_keyword():
    with pytest.raises(TypeError) as exc_info:
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=_fail_if_called,
            label="mine",
            onBinary="include",
            maxBytes=10,
        )
    message = str(exc_info.value)
    assert message == (
        "filter_relevant options are snake_case in Python. "
        "onBinary: use on_binary; "
        "maxBytes: use max_bytes"
    )
    assert "label" not in message


def test_unknown_non_camel_case_keyword_still_fails():
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'label'"):
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=_fail_if_called,
            label="mine",
        )


def test_snake_case_alias_of_retry_base_ms_is_an_unknown_keyword():
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'retry_base_ms'"):
        filter_relevant(
            {"a.py": "x = 1\n"},
            "task",
            cache=False,
            api_key="k",
            call_llm=_fail_if_called,
            retry_base_ms=200,
        )


class _YesResponse:
    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _Message:
        content = "yes"

    class _Choice:
        message = None

    def __init__(self):
        choice = self._Choice()
        choice.message = self._Message()
        self.choices = [choice]
        self.usage = self._Usage()


def _classify(files, **options):
    """Run the filter offline. Returns ``(result, call_count)``."""

    def fake_call_llm(*_args, **_kwargs):
        fake_call_llm.n += 1
        return _YesResponse()

    fake_call_llm.n = 0
    result = filter_relevant(
        files,
        "task",
        model="fake",
        cache=False,
        api_key="k",
        call_llm=fake_call_llm,
        **options,
    )
    return result, fake_call_llm.n


def _ignored(result):
    return {item.path for item in result.decisions if item.skipped_reason == "ignored"}


def test_skip_patterns_none_applies_the_default_list():
    result, calls = _classify(
        {"uv.lock": "lock\n", "src/app.py": "x = 1\n"},
        skip_patterns=None,
    )
    assert _ignored(result) == {"uv.lock"}
    assert result.decisions[0].decision == "no"
    assert "uv.lock" not in result.files
    assert "src/app.py" in result.files
    assert calls == 1


@pytest.mark.parametrize("empty", [[], ()])
def test_skip_patterns_empty_sequence_classifies_every_path(empty):
    files = {"uv.lock": "lock\n", "dist/app.js": "x\n", "src/app.py": "x = 1\n"}
    result, calls = _classify(files, skip_patterns=empty)
    assert _ignored(result) == set()
    assert set(result.files) == set(files)
    assert calls == 3


def test_skip_patterns_custom_list_replaces_defaults():
    result, calls = _classify(
        {"uv.lock": "lock\n", "notes.tmp": "temp\n", "src/app.py": "x = 1\n"},
        skip_patterns=["*.tmp"],
    )
    assert _ignored(result) == {"notes.tmp"}
    assert "uv.lock" in result.files
    assert "notes.tmp" not in result.files
    assert calls == 2


def test_skip_patterns_match_globs_not_regex_or_substring():
    # ``*`` does not cross ``/``, the match is the whole path, and case is folded.
    result, _calls = _classify(
        {
            "uv.lock": "a\n",
            "dir/uv.lock": "a\n",
            "file.lock.txt": "a\n",
            "Notes.TMP": "a\n",
            "src/keep.py": "a\n",
        },
        skip_patterns=["*.lock", "*.TMP"],
    )
    assert _ignored(result) == {"uv.lock", "Notes.TMP"}

    # ``**`` crosses directories.
    result, _calls = _classify(
        {"dir/uv.lock": "a\n", "uv.lock": "a\n", "src/keep.py": "a\n"},
        skip_patterns=["**/uv.lock"],
    )
    assert _ignored(result) == {"dir/uv.lock", "uv.lock"}

    # ``.*`` as a regex would match every name. As a glob it is a leading dot.
    result, _calls = _classify(
        {"a.py": "a\n", ".env": "a\n", "dir/.env": "a\n"},
        skip_patterns=[".*"],
    )
    assert _ignored(result) == {".env"}

    # A bare token is not a substring of a longer name.
    result, _calls = _classify(
        {"uv.lock": "a\n", "lock": "a\n"},
        skip_patterns=["lock"],
    )
    assert _ignored(result) == {"lock"}
