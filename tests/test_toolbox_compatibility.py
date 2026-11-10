"""Exercise the installed Toolbox release without mocking its classes or events."""

from importlib.metadata import requires, version
import os
from types import SimpleNamespace

from packaging.requirements import Requirement
import pytest

from ai_agent_toolbox import Toolbox, XMLParser
from patchling import core


def test_installed_toolbox_matches_ci_selection_and_package_metadata():
    installed = version("ai-agent-toolbox")
    selected = os.environ.get("TOOLBOX_VERSION")
    if selected and selected != "latest":
        assert installed == selected
    requirement = next(
        Requirement(item) for item in requires("patchling")
        if Requirement(item).name == "ai-agent-toolbox"
    )
    assert requirement.specifier.contains(installed)
    assert not any(spec.operator in {"<", "<="} for spec in requirement.specifier)
    assert core.Toolbox is Toolbox
    assert core.XMLParser is XMLParser


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("Prose\n```diff\nfirst\n```\n```python\nignored\n```\n"
         "```diff\nsecond\n```", "first\n\nsecond\n"),
        ("```diffx\nignored\n```\n```diff\nkept\n```", "kept\n"),
        ("```diff\r\nkept\r\n```", "kept\n"),
        ("```diff\nunterminated", "unterminated\n"),
        ("no applicable fence", ""),
    ],
)
def test_diff_generation_with_real_installed_toolbox(
    monkeypatch, fake_tiktoken, response, expected
):
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )
    monkeypatch.setattr(core, "call_llm", lambda **kwargs: completion)
    full, diff, prompt, completion_tokens, total = core.call_llm_for_diff(
        "system", "goal", "files", "test-model", api_key="dummy-key"
    )
    assert full == response.strip()
    assert diff == expected
    assert (prompt, completion_tokens, total) == (2, 3, 5)


def test_generated_multi_file_diff_applies_end_to_end(monkeypatch, fake_tiktoken):
    def unexpected_apply(*args, **kwargs):
        raise AssertionError("These clean diffs must not need a second model call")

    response = (
        "First file:\n```diff\n--- a/a.txt\n+++ b/a.txt\n"
        "@@ -1 +1 @@\n-old-a\n+new-a\n```\n"
        "Second file:\n```diff\n--- a/b.txt\n+++ b/b.txt\n"
        "@@ -1 +1 @@\n-old-b\n+new-b\n```"
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
        usage=None,
    )
    monkeypatch.setattr(core, "call_llm", lambda **kwargs: completion)
    monkeypatch.setattr(core, "call_llm_for_apply", unexpected_apply)
    original = {"a.txt": "old-a\n", "b.txt": "old-b\n", "untouched.txt": "keep\n"}
    diff = core.generate_diff(
        core.build_environment(original), "update a and b", api_key="dummy-key"
    )
    assert core.smartapply(diff, original) == {
        "a.txt": "new-a\n", "b.txt": "new-b\n", "untouched.txt": "keep\n"
    }
    assert original == {"a.txt": "old-a\n", "b.txt": "old-b\n", "untouched.txt": "keep\n"}


@pytest.mark.parametrize(
    ("response", "expected", "dispatches_tool"),
    [
        ("new content", "new content", False),
        ("<think>private reasoning</think>\nnew content", "new content", False),
        ("<think><name>think</name><content>plan π</content></think>\nnew content",
         "new content", True),
    ],
)
def test_apply_uses_real_xml_parser_and_toolbox(
    monkeypatch, capsys, response, expected, dispatches_tool
):
    monkeypatch.setattr(core, "call_llm_for_apply", lambda *args, **kwargs: response)
    actual = core.call_llm_for_apply_with_think_tool_available(
        "file.txt", "old content", "diff", "test-model", max_tokens=128
    )
    assert actual == expected
    if dispatches_tool:
        assert "Swallowed thoughts plan π" in capsys.readouterr().out


def test_apply_provider_failure_is_not_hidden_by_toolbox(monkeypatch):
    failure = RuntimeError("provider unavailable")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(core, "call_llm_for_apply", fail)
    with pytest.raises(RuntimeError) as caught:
        core.call_llm_for_apply_with_think_tool_available(
            "file.txt", "old", "diff", "test-model", max_tokens=128
        )
    assert caught.value is failure
