"""Public API parity with 255BITS/patchling (the JavaScript package)."""

from pathlib import Path

import patchling
from patchling import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    SmartApplyError,
    apply_diff,
    build_environment,
    call_llm,
    call_llm_for_diff,
    color_code_diff,
    extract_diff_blocks,
    generate_diff,
    get_env,
    load_project_files,
    parse_diff_per_file,
    resolve_api_key,
    resolve_base_url,
    save_files,
    set_env,
    smartapply,
    strip_bad_output,
    strip_think_tags,
    swallow_reasoning,
)


EXPORTED = [
    "apply_diff",
    "parse_diff_per_file",
    "extract_diff_blocks",
    "color_code_diff",
    "strip_think_tags",
    "swallow_reasoning",
    "strip_bad_output",
    "call_llm",
    "call_llm_for_diff",
    "resolve_api_key",
    "resolve_base_url",
    "get_env",
    "set_env",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
    "generate_diff",
    "smartapply",
    "load_project_files",
    "build_environment",
    "save_files",
    "SmartApplyError",
]


def test_package_exports_match_the_js_surface():
    for name in EXPORTED:
        assert hasattr(patchling, name), name
        assert name in patchling.__all__


def test_existing_names_still_import_from_the_package():
    assert callable(generate_diff)
    assert callable(smartapply)
    assert callable(load_project_files)
    assert callable(build_environment)
    assert callable(save_files)
    assert callable(apply_diff)
    assert callable(parse_diff_per_file)
    assert callable(call_llm)
    assert callable(call_llm_for_diff)
    assert issubclass(SmartApplyError, Exception)


def test_py_typed_marker_is_packaged():
    marker = Path(patchling.__file__).resolve().parent / "py.typed"
    assert marker.is_file()


def test_defaults_match_the_library_fallback():
    assert DEFAULT_MODEL == "deepseek-reasoner"
    assert DEFAULT_BASE_URL == "https://nano-gpt.com/api/v1/"


def test_set_env_overrides_the_process_environment(monkeypatch):
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "from-env")
    monkeypatch.setenv("GPTDIFF_LLM_BASE_URL", "https://env.example/v1")
    set_env("GPTDIFF_LLM_API_KEY", None)
    set_env("GPTDIFF_LLM_BASE_URL", None)
    assert get_env("GPTDIFF_LLM_API_KEY") == "from-env"
    assert resolve_api_key() == "from-env"
    assert resolve_base_url() == "https://env.example/v1"

    set_env("GPTDIFF_LLM_API_KEY", "from-override")
    set_env("GPTDIFF_LLM_BASE_URL", "https://override.example/v1")
    try:
        assert get_env("GPTDIFF_LLM_API_KEY") == "from-override"
        assert resolve_api_key() == "from-override"
        assert resolve_api_key("explicit") == "explicit"
        assert resolve_base_url() == "https://override.example/v1"
        assert resolve_base_url("https://explicit.example/v1") == "https://explicit.example/v1"
    finally:
        set_env("GPTDIFF_LLM_API_KEY", None)
        set_env("GPTDIFF_LLM_BASE_URL", None)
    assert get_env("GPTDIFF_LLM_API_KEY") == "from-env"


def test_resolve_base_url_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("GPTDIFF_LLM_BASE_URL", raising=False)
    set_env("GPTDIFF_LLM_BASE_URL", None)
    assert resolve_base_url() == DEFAULT_BASE_URL
    assert resolve_base_url("") == DEFAULT_BASE_URL


def test_get_env_returns_fallback_when_unset(monkeypatch):
    monkeypatch.delenv("GPTDIFF_NOT_A_REAL_VAR", raising=False)
    set_env("GPTDIFF_NOT_A_REAL_VAR", None)
    assert get_env("GPTDIFF_NOT_A_REAL_VAR") is None
    assert get_env("GPTDIFF_NOT_A_REAL_VAR", "fallback") == "fallback"


def test_generate_diff_reads_model_from_env_override(monkeypatch):
    seen = {}

    def fake_call(system_prompt, user_prompt, files_content, model, **kwargs):
        seen["model"] = model
        seen["api_key"] = kwargs.get("api_key")
        seen["base_url"] = kwargs.get("base_url")
        return "", "diff-body", 1, 2, 3

    monkeypatch.setattr("patchling.core.call_llm_for_diff", fake_call)
    monkeypatch.delenv("GPTDIFF_MODEL", raising=False)
    set_env("GPTDIFF_MODEL", None)
    try:
        assert generate_diff("File: a.py\nContent:\nx\n", "rename it") == "diff-body"
        assert seen["model"] == DEFAULT_MODEL

        set_env("GPTDIFF_MODEL", "unit-test-model")
        generate_diff("File: a.py\nContent:\nx\n", "rename it", api_key="k", base_url="https://b.example/v1")
        assert seen["model"] == "unit-test-model"
        assert seen["api_key"] == "k"
        assert seen["base_url"] == "https://b.example/v1"
    finally:
        set_env("GPTDIFF_MODEL", None)


def test_call_llm_for_apply_uses_resolved_credentials(monkeypatch):
    captured = {}

    class _Message:
        content = "kept\n"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None, base_url=None, max_retries=0):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = _Chat()

    monkeypatch.setattr("patchling.core.OpenAI", _Client)
    set_env("GPTDIFF_LLM_API_KEY", "override-key")
    set_env("GPTDIFF_LLM_BASE_URL", "https://override.example/v1")
    try:
        text = patchling.core.call_llm_for_apply(
            "a.py",
            "x = 1\n",
            "--- a/a.py\n+++ b/a.py\n",
            "unit-model",
        )
    finally:
        set_env("GPTDIFF_LLM_API_KEY", None)
        set_env("GPTDIFF_LLM_BASE_URL", None)
    assert text == "kept\n"
    assert captured["api_key"] == "override-key"
    assert captured["base_url"] == "https://override.example/v1"
    assert captured["create"]["model"] == "unit-model"
    assert captured["create"]["temperature"] == 0.0


def test_text_helpers_match_the_js_contract():
    colored = color_code_diff("-old\n+new\n same\n")
    assert colored.startswith("\033[31m-old")
    assert "\033[32m+new" in colored
    assert " same" in colored

    final, reasoning = swallow_reasoning(
        "> Reasoning\nstep\nReasoned for 3 seconds\n\nprint('ok')\n"
    )
    assert final == "print('ok')"
    assert "step" in reasoning

    assert strip_think_tags("<think>secret</think>\n\ndef f():\n    return 1\n") == "def f():\n    return 1"
    assert strip_think_tags("keep\n<think>never closed") == "keep"
    assert "Reasoned" not in strip_think_tags(
        "> Reasoning\nhidden\nReasoned for 1 seconds\n\nvisible\n"
    )

    wrapped = "here is the file:\n```python\nvalue = 1\n```\n"
    assert strip_bad_output(wrapped, "value = 0\n") == "value = 1\n"

    blocks = extract_diff_blocks("prose\n```diff\n--- a/a.py\n+++ b/a.py\n```\n")
    assert len(blocks) == 1
    assert "--- a/a.py" in blocks[0]


# Relevance helpers and filter defaults, exported at the package level the
# way src/index.js exports them in 255BITS/patchling (JS names in snake_case;
# constants keep the JS DEFAULT_FILTER_* names).
RELEVANCE_EXPORTS = {
    "count_tokens": "count_tokens",
    "is_binary_content": "is_binary_content",
    "parse_relevance_answer": "parse_relevance_answer",
    "path_is_skipped": "path_is_skipped",
    "format_filter_summary": "format_filter_summary",
    "dump_decisions": "dump_decisions",
    "DEFAULT_FILTER_MODEL": "DEFAULT_FILTER_MODEL",
    "DEFAULT_FILTER_CONCURRENCY": "DEFAULT_CONCURRENCY",
    "DEFAULT_FILTER_MAX_BYTES": "DEFAULT_MAX_BYTES",
    "DEFAULT_FILTER_MAX_TOKENS": "DEFAULT_MAX_TOKENS",
    "DEFAULT_FILTER_MAX_OUTPUT_TOKENS": "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_FILTER_REASONING_EFFORT": "REASONING_EFFORT",
    "DEFAULT_SKIP_PATTERNS": "DEFAULT_SKIP_PATTERNS",
    "PROMPT_VERSION": "PROMPT_VERSION",
    "FILTER_PROMPT_VERSION": "PROMPT_VERSION",
}


def _relevance_fixture():
    import json

    path = Path(__file__).resolve().parent / "fixtures" / "parity" / "relevance.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_relevance_helpers_are_exported_from_the_package():
    import patchling.relevance as relevance

    for public, internal in RELEVANCE_EXPORTS.items():
        assert hasattr(patchling, public), public
        assert public in patchling.__all__, public
        assert getattr(patchling, public) is getattr(relevance, internal), public


def test_all_names_resolve_and_are_unique():
    assert len(patchling.__all__) == len(set(patchling.__all__))
    for name in patchling.__all__:
        assert hasattr(patchling, name), name


def test_count_tokens_matches_the_shared_token_estimate_fixture():
    estimate = _relevance_fixture()["tokenEstimate"]
    assert estimate["rule"] == "ceil(code_points/4)"
    assert patchling.count_tokens("") == estimate["empty"]
    assert estimate["examples"], "fixture has no tokenEstimate examples"
    for example in estimate["examples"]:
        assert patchling.count_tokens(example["text"]) == example["tokens"], example["text"]


def test_filter_defaults_match_the_shared_fixture():
    fixture = _relevance_fixture()
    assert str(patchling.PROMPT_VERSION) == str(fixture["promptVersion"])
    assert patchling.FILTER_PROMPT_VERSION == patchling.PROMPT_VERSION
    assert patchling.DEFAULT_FILTER_MAX_OUTPUT_TOKENS == fixture["maxOutputTokens"]
    assert patchling.DEFAULT_FILTER_REASONING_EFFORT == fixture["reasoningEffort"]
    assert list(patchling.DEFAULT_SKIP_PATTERNS) == fixture["ignore"]["patterns"]
    assert patchling.DEFAULT_FILTER_CONCURRENCY == 8
    assert patchling.DEFAULT_FILTER_MAX_BYTES == 200_000
    assert patchling.DEFAULT_FILTER_MAX_TOKENS == 12_000


def test_exported_relevance_helpers_behave(tmp_path):
    import json

    assert patchling.is_binary_content(b"a\x00b") is True
    assert patchling.is_binary_content("plain text\n") is False
    assert patchling.parse_relevance_answer("Yes.")[0] == "yes"
    assert patchling.parse_relevance_answer("maybe")[0] is None
    assert patchling.path_is_skipped("web/package-lock.json", patchling.DEFAULT_SKIP_PATTERNS)
    assert not patchling.path_is_skipped("src/app.py", patchling.DEFAULT_SKIP_PATTERNS)

    result = patchling.FilterRelevantResult(
        files={"a.py": "x = 1\n"},
        decisions=[
            patchling.RelevanceDecision(path="a.py", decision="yes", tokens=5),
            patchling.RelevanceDecision(path="b.py", decision="no", tokens=3),
        ],
        model="unit-model",
    )
    summary = patchling.format_filter_summary(result)
    assert summary.startswith("Relevance filter (unit-model): kept 1/2 files, 8 tokens")

    out = tmp_path / "decisions.json"
    patchling.dump_decisions(result, out)
    dumped = json.loads(out.read_text(encoding="utf-8"))
    assert dumped["files"] == ["a.py"]
    assert [d["path"] for d in dumped["decisions"]] == ["a.py", "b.py"]
