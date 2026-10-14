"""CamelCase option names from the JS filter are rejected with Python spellings.

No network. The injected client fails the test if ``filter_relevant`` calls it.
"""

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


def _recommended(guidance):
    match = _USE_NAME.match(guidance)
    if match is None:
        return None
    return match.group(1)


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
    """Each JS ``snake -> use camel`` entry inverts to ``camel -> use snake``.

    ``sleep`` has no camelCase spelling. ``retry_base_ms`` is an alias of
    ``retryBaseMs``; the Python parameter behind that name is ``retry_backoff``.
    """
    js_pairs = {}
    aliases = {}
    for snake, guidance in JS_SNAKE_CASE_OPTIONS.items():
        camel = _recommended(guidance)
        if camel is None:
            assert snake == "sleep"
            assert "not supported" in guidance
            continue
        if camel in js_pairs:
            aliases.setdefault(camel, []).append(snake)
            continue
        js_pairs[camel] = snake

    py_pairs = {
        camel: _recommended(guidance)
        for camel, guidance in CAMEL_CASE_OPTIONS.items()
    }
    assert None not in py_pairs.values()
    assert py_pairs == js_pairs
    assert aliases == {"retryBaseMs": ["retry_base_ms"]}
    assert set(CAMEL_CASE_OPTIONS) == set(js_pairs)
    for camel, snake in py_pairs.items():
        assert _recommended(JS_SNAKE_CASE_OPTIONS[snake]) == camel
        assert snake in CAMEL_CASE_OPTIONS[camel]

    assert "max_attempts = retries + 1" in CAMEL_CASE_OPTIONS["retries"]
    assert "retries = max_attempts - 1" in JS_SNAKE_CASE_OPTIONS["max_attempts"]
    assert "retry_backoff = retryBaseMs / 1000" in CAMEL_CASE_OPTIONS["retryBaseMs"]
    assert "retryBaseMs = retry_backoff * 1000" in JS_SNAKE_CASE_OPTIONS["retry_backoff"]
    assert CAMEL_CASE_OPTIONS["ignore"].startswith("use skip_patterns")
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
