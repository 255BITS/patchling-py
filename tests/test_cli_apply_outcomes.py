"""Filesystem CLI outcomes use exit status, not merely printed failure text."""

from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

import patchling.core as core
import patchling.gptpatch as apply_cli


def change(path, old="old", new="updated"):
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def deletion(path):
    return f"--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"


def options(**overrides):
    return SimpleNamespace(applymodel=None, max_tokens=None, beep=False, **overrides)


@pytest.fixture(autouse=True)
def offline_provider(monkeypatch):
    monkeypatch.setenv("GPTDIFF_LLM_API_KEY", "test-key")
    monkeypatch.delenv("GPTDIFF_SMARTAPPLY_MODEL", raising=False)
    monkeypatch.delenv("GPTDIFF_SMARTAPPLY_API_KEY", raising=False)
    monkeypatch.delenv("GPTDIFF_SMARTAPPLY_BASE_URL", raising=False)

    def unexpected(*args, **kwargs):
        pytest.fail("a CLI regression test attempted an unmocked model call")

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", unexpected)


def invoke(monkeypatch, tmp_path, entrypoint, diff, extra=(), generation_models=None):
    """Run the real parser and command while completion/tokenization are mocked."""
    if entrypoint == "apply":
        monkeypatch.setattr(sys, "argv", [
            "patchling-apply", "--diff", diff, "--project-dir", str(tmp_path),
            "--nobeep", *extra,
        ])
        return apply_cli.main()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "patchling", "update the project", "--apply", "--nowarn", "--nobeep", *extra,
    ])
    monkeypatch.setattr(core, "load_project_files", lambda *_: [
        (str(path), path.read_text()) for path in tmp_path.glob("*.txt")
    ])
    def generate(_system, _prompt, _files, model, **kwargs):
        if generation_models is not None:
            generation_models.append(model)
        return "mock completion", diff, 2, 3, 5

    monkeypatch.setattr(core, "call_llm_for_diff", generate)
    return core.main()


def test_helper_reports_successes_and_failure_details(tmp_path, monkeypatch, capsys):
    (tmp_path / "good.txt").write_text("old\n")
    (tmp_path / "bad.txt").write_text("old\n")

    def complete(path, *args, **kwargs):
        if path == "bad.txt":
            raise core.SmartApplyError("rewrite refused")
        return "updated"

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", complete)
    result = core.smart_apply_patch(tmp_path, change("good.txt") + change("bad.txt"), "edit", options())
    assert not result.ok
    assert result.succeeded == ("good.txt",)
    assert result.failures == {"bad.txt": "rewrite refused"}
    assert (tmp_path / "good.txt").read_text() == "updated\n"
    assert (tmp_path / "bad.txt").read_text() == "old\n"
    output = capsys.readouterr().out
    assert "with failures for 1 files" in output
    assert "Smartapply successfully applied changes" not in output
    assert "Successful file changes may remain" in output


@pytest.mark.parametrize("entrypoint", ["apply", "generate"])
def test_both_clis_return_failure_after_partial_apply(tmp_path, monkeypatch, fake_tiktoken, capsys, entrypoint):
    (tmp_path / "good.txt").write_text("old\n")
    (tmp_path / "bad.txt").write_text("old\n")

    def complete(path, *args, **kwargs):
        if path == "bad.txt":
            raise core.SmartApplyError("refused")
        return "updated"

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", complete)
    assert invoke(monkeypatch, tmp_path, entrypoint, change("good.txt") + change("bad.txt")) == 1
    assert (tmp_path / "good.txt").read_text() == "updated\n"
    assert (tmp_path / "bad.txt").read_text() == "old\n"
    if entrypoint == "generate":
        assert "API Usage: 5 tokens" in capsys.readouterr().out


@pytest.mark.parametrize("entrypoint", ["apply", "generate"])
@pytest.mark.parametrize("diff", ["", "not a diff", change("../escaped.txt")])
def test_both_clis_fail_on_empty_invalid_and_unsafe_diffs(tmp_path, monkeypatch, fake_tiktoken, entrypoint, diff):
    (tmp_path / "keep.txt").write_text("unchanged\n")
    assert invoke(monkeypatch, tmp_path, entrypoint, diff) == 1
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert (tmp_path / "keep.txt").read_text() == "unchanged\n"


@pytest.mark.parametrize("entrypoint", ["apply", "generate"])
def test_both_clis_succeed_for_deletions_without_a_model_call(tmp_path, monkeypatch, fake_tiktoken, entrypoint):
    (tmp_path / "gone.txt").write_text("old\n")
    diff = deletion("gone.txt") + deletion("already-gone.txt")
    assert invoke(monkeypatch, tmp_path, entrypoint, diff) == 0
    assert not (tmp_path / "gone.txt").exists()


def test_helper_reports_deleted_and_already_absent_files(tmp_path):
    (tmp_path / "gone.txt").write_text("old\n")
    result = core.smart_apply_patch(tmp_path, deletion("gone.txt") + deletion("missing.txt"), "", options())
    assert result.ok
    assert result.succeeded == ("gone.txt",)
    assert result.skipped == ("missing.txt",)


def test_deletion_io_error_fails_the_aggregate(tmp_path, monkeypatch):
    target = tmp_path / "gone.txt"
    target.write_text("old\n")
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == target:
            raise PermissionError("deletion denied")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", unlink)
        result = core.smart_apply_patch(tmp_path, deletion("gone.txt"), "", options())
    assert not result.ok
    assert result.failures == {"gone.txt": "deletion denied"}
    assert target.exists()


def test_failed_read_does_not_become_a_new_file_model_call(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("old\n")
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == target:
            raise PermissionError("read denied")
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", read)
        result = core.smart_apply_patch(tmp_path, change("file.txt"), "", options())
    assert not result.ok
    assert result.failures == {"file.txt": "read denied"}
    assert target.read_text() == "old\n"


def test_write_error_fails_the_aggregate(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("old\n")
    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", lambda *_a, **_k: "updated")
    original_write = Path.write_text

    def write(path, *args, **kwargs):
        if path == target:
            raise OSError("disk full")
        return original_write(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", write)
        result = core.smart_apply_patch(tmp_path, change("file.txt"), "", options())
    assert not result.ok
    assert result.failures == {"file.txt": "disk full"}
    assert target.read_text() == "old\n"


def test_empty_model_response_leaves_original_and_fails(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("old\n")
    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", lambda *_a, **_k: "  ")
    result = core.smart_apply_patch(tmp_path, change("file.txt"), "", options())
    assert not result.ok
    assert "empty model response" in result.failures["file.txt"]
    assert target.read_text() == "old\n"


def test_apply_cli_model_flag_overrides_apply_environment(tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("old\n")
    monkeypatch.setenv("GPTDIFF_SMARTAPPLY_MODEL", "environment-model")
    models = []

    def complete(_path, _content, _diff, model, **kwargs):
        models.append(model)
        return "updated"

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", complete)
    assert invoke(monkeypatch, tmp_path, "apply", change("file.txt"), ["--model", "requested-model"]) == 0
    assert models == ["requested-model"]


@pytest.mark.parametrize("apply_model,expected", [(None, "environment-model"), ("requested-apply", "requested-apply")])
def test_generate_cli_keeps_diff_and_apply_model_selection_separate(tmp_path, monkeypatch, fake_tiktoken, apply_model, expected):
    (tmp_path / "file.txt").write_text("old\n")
    monkeypatch.setenv("GPTDIFF_SMARTAPPLY_MODEL", "environment-model")
    models = []

    def complete(_path, _content, _diff, model, **kwargs):
        models.append(model)
        return "updated"

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", complete)
    extra = ["--model", "diff-generation-model"]
    generation_models = []
    if apply_model:
        extra += ["--applymodel", apply_model]
    assert invoke(monkeypatch, tmp_path, "generate", change("file.txt"), extra, generation_models) == 0
    assert generation_models == ["diff-generation-model"]
    assert models == [expected]


def test_dumb_success_avoids_smart_apply(tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("old\n")
    assert invoke(monkeypatch, tmp_path, "apply", change("file.txt"), ["--dumb"]) == 0
    assert (tmp_path / "file.txt").read_text() == "updated\n"


def test_dumb_failure_propagates_smart_apply_failure(tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("old\n")
    monkeypatch.setattr(apply_cli, "apply_diff", lambda *_a: False)

    def refused(*args, **kwargs):
        raise core.SmartApplyError("refused")

    monkeypatch.setattr(core, "call_llm_for_apply_with_think_tool_available", refused)
    assert invoke(monkeypatch, tmp_path, "apply", change("file.txt"), ["--dumb"]) == 1


@pytest.mark.parametrize("entrypoint", ["apply", "generate"])
def test_console_entrypoint_process_exits_nonzero_on_refusal(tmp_path, entrypoint):
    target = tmp_path / "file.txt"
    target.write_text("old\n")
    script = r'''
from pathlib import Path
from types import SimpleNamespace
import sys
import patchling.core as core
import patchling.gptpatch as apply_cli
root, entrypoint = Path(sys.argv[1]), sys.argv[2]
diff = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+updated\n"
def refuse(*args, **kwargs):
    raise core.SmartApplyError("subprocess refusal")
core.call_llm_for_apply_with_think_tool_available = refuse
core.call_llm_for_diff = lambda *a, **k: ("mock response", diff, 1, 1, 2)
core.load_project_files = lambda *a: [(str(root / "file.txt"), "old\n")]
core.tiktoken.get_encoding = lambda *a: SimpleNamespace(encode=lambda text: list(text))
if entrypoint == "apply":
    sys.argv = ["patchling-apply", "--diff", diff, "--project-dir", str(root), "--nobeep"]
    main = apply_cli.main
else:
    sys.argv = ["patchling", "edit", "--apply", "--nowarn", "--nobeep"]
    main = core.main
# The installed console scripts use this same sys.exit(main()) contract.
sys.exit(main())
'''
    env = dict(os.environ, GPTDIFF_LLM_API_KEY="test-key", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), entrypoint],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert "subprocess refusal" in result.stdout
    assert "with failures for 1 files" in result.stdout
    assert "Traceback" not in result.stderr
    assert target.read_text() == "old\n"


def test_apply_module_exits_nonzero_for_explicitly_empty_diff(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-m", "patchling.gptpatch", "--diff", "", "--nobeep"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "There were no entries in this diff" in result.stdout
    assert "Traceback" not in result.stderr
