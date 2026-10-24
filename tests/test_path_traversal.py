"""Regression tests: diffs/file maps must never write outside the project root."""
import os
from pathlib import Path

import pytest

from patchling.applydiff import apply_diff, resolve_within, UnsafePathError
from patchling.core import save_files


def _new_file_diff(path):
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        "+pwned\n"
    )


@pytest.fixture
def layout(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return tmp_path, proj


def test_apply_diff_rejects_dotdot(layout):
    tmp, proj = layout
    assert apply_diff(proj, _new_file_diff("../escaped.txt")) is False
    assert not (tmp / "escaped.txt").exists()


def test_apply_diff_rejects_nested_dotdot(layout):
    tmp, proj = layout
    assert apply_diff(proj, _new_file_diff("sub/../../escaped.txt")) is False
    assert not (tmp / "escaped.txt").exists()


def test_apply_diff_rejects_absolute(layout):
    tmp, proj = layout
    target = tmp / "abs_escaped.txt"
    assert apply_diff(proj, _new_file_diff(str(target))) is False
    assert not target.exists()


def test_apply_diff_rejects_symlink_escape(layout):
    tmp, proj = layout
    outside = tmp / "outside"
    outside.mkdir()
    os.symlink(outside, proj / "link")
    assert apply_diff(proj, _new_file_diff("link/escaped.txt")) is False
    assert not (outside / "escaped.txt").exists()


def test_apply_diff_still_allows_inside(layout):
    _, proj = layout
    assert apply_diff(proj, _new_file_diff("sub/ok.txt")) is True
    assert (proj / "sub" / "ok.txt").read_text().strip() == "pwned"


@pytest.mark.parametrize("bad", ["../escaped.txt", "a/../../escaped.txt"])
def test_save_files_rejects_dotdot(layout, bad):
    tmp, proj = layout
    with pytest.raises(UnsafePathError):
        save_files({bad: "x"}, proj)
    assert not (tmp / "escaped.txt").exists()


def test_save_files_rejects_absolute(layout):
    tmp, proj = layout
    target = tmp / "abs.txt"
    with pytest.raises(UnsafePathError):
        save_files({str(target): "x"}, proj)
    assert not target.exists()


def test_save_files_allows_inside(layout):
    _, proj = layout
    save_files({"a/b.txt": "x"}, proj)
    assert (proj / "a" / "b.txt").read_text() == "x"


def test_resolve_within_root_itself_ok(layout):
    _, proj = layout
    assert resolve_within(proj, "x.txt") == Path(proj) / "x.txt"
