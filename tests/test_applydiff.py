import os
from pathlib import Path

import pytest

from patchling.core import apply_diff


@pytest.fixture
def tmp_project_dir_with_file(tmp_path):
    """
    Create a temporary project directory with a dummy file to patch.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    file = project_dir / "example.txt"
    file.write_text("original content\n")
    return project_dir


def test_apply_diff_success(tmp_project_dir_with_file):
    """
    Test that apply_diff successfully applies a valid diff.
    The diff changes 'original content' to 'modified content'.
    """
    diff_text = (
        "diff --git a/example.txt b/example.txt\n"
        "--- a/example.txt\n"
        "+++ a/example.txt\n"
        "@@ -1 +1 @@\n"
        "-original content\n"
        "+modified content\n"
    )
    result = apply_diff(str(tmp_project_dir_with_file), diff_text)
    assert result is True, "apply_diff should return True for a successful patch"

    file_path = tmp_project_dir_with_file / "example.txt"
    content = file_path.read_text()
    assert "modified content" in content, "File content should be updated to 'modified content'"


def test_apply_diff_failure(tmp_project_dir_with_file):
    """
    Test that apply_diff fails when provided with an incorrect hunk header.
    The diff references a non-existent line, so the patch should not apply.
    """
    diff_text = (
        "diff --git a/example.txt b/example.txt\n"
        "--- a/example.txt\n"
        "+++ a/example.txt\n"
        "@@ -2,1 +2,1 @@\n"
        "-original content\n"
        "+modified content\n"
    )
    result = apply_diff(str(tmp_project_dir_with_file), diff_text)
    assert result is False, "apply_diff should return False when the diff fails to apply"

    file_path = tmp_project_dir_with_file / "example.txt"
    content = file_path.read_text()
    assert "original content" in content, "File content should remain unchanged on failure"


def test_apply_diff_file_deletion(tmp_project_dir_with_file):
    """
    Test that apply_diff can successfully delete a file.
    The diff marks 'example.txt' for deletion.
    """
    diff_text = (
        "diff --git a/example.txt b/example.txt\n"
        "deleted file mode 100644\n"
        "--- a/example.txt\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-original content\n"
    )
    result = apply_diff(str(tmp_project_dir_with_file), diff_text)
    assert result is True, "apply_diff should return True for a successful file deletion"

    file_path = tmp_project_dir_with_file / "example.txt"
    assert not file_path.exists(), "File should be deleted after applying the diff"


@pytest.fixture
def tmp_project_dir_empty(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return project_dir


def test_minimal_new_file_diff(tmp_project_dir_empty):
    diff_text = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@\n"
        "+hello world\n"
    )
    result = apply_diff(str(tmp_project_dir_empty), diff_text)
    assert result is True
    new_file = tmp_project_dir_empty / "new.txt"
    assert new_file.exists()
    assert new_file.read_text() == "hello world\n"

import pytest
from pathlib import Path
from patchling.core import apply_diff

@pytest.fixture
def tmp_project_dir_empty(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return project_dir

def test_new_file_creation_minimal_header_failure(tmp_project_dir_empty):
    """
    Test that a minimal diff for new file creation (with a hunk header that lacks line numbers)
    creates the file with the expected content.

    Diff text:
        --- /dev/null
        +++ b/test_feature_1739491796.py
        @@
        +import pytest
        +

    Expected result: a new file "test_feature_1739491796.py" containing "import pytest\n"
    """
    diff_text = (
        "--- /dev/null\n"
        "+++ b/test_feature_1739491796.py\n"
        "@@\n"
        "+import pytest\n"
        "+\n"
    )
    result = apply_diff(str(tmp_project_dir_empty), diff_text)
    assert result is True, "apply_diff should return True for a successful new file creation"
    new_file = tmp_project_dir_empty / "test_feature_1739491796.py"
    assert new_file.exists(), "New file should be created"
    expected_content = "import pytest\n"
    content = new_file.read_text()
    assert content.strip() == expected_content.strip(), f"Expected file content:\n{expected_content}\nGot:\n{content}"

@pytest.fixture
def tmp_project_dir_with_gptdiff(tmp_path):
    """
    Create a temporary project directory with a gptdiff.py file containing four lines.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    file = project_dir / "gptdiff.py"
    file.write_text("#!/usr/bin/env python3\nfrom pathlib import Path\n# Line 3\n# Line 4\n")
    return project_dir

def test_apply_bad_diff_fails(tmp_project_dir_with_gptdiff):
    """
    Test that a diff is applied correctly to the file.
    """
    diff_text = """diff --git a/gptdiff/gptdiff.py b/gptdiff/gptdiff.py
index 1234567..89abcde 100644
--- a/gptdiff/gptdiff.py
+++ b/gptdiff/gptdiff.py
@@ -1,4 +1,5 @@
 #!/usr/bin/env python3
+from threading import Lock
 from pathlib import Path"""

    # Assume apply_diff is a function that applies the diff
    from patchling.core import apply_diff
    result = apply_diff(str(tmp_project_dir_with_gptdiff), diff_text)
    assert result is False, "apply_diff should fail, needs smartapply"
