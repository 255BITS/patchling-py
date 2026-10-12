"""```diff fence extraction, matching 255BITS/patchling extractDiffBlocks."""

from patchling.applydiff import extract_diff_blocks
from patchling.core import apply_diff, parse_diff_per_file


def test_extract_diff_blocks_requires_exact_diff_language():
    # Same cases as the JS parity test: only language "diff" is collected,
    # and the body keeps the newline that precedes the closing fence.
    assert extract_diff_blocks("```diffx\nhello\n```") == []
    assert extract_diff_blocks("```python\nhello\n```") == []
    assert extract_diff_blocks("```diff\nhello\n```") == ["hello\n"]


def test_extract_diff_blocks_force_closes_unterminated_block():
    assert extract_diff_blocks("```diff\nhello\nworld") == ["hello\nworld\n"]


def test_extract_diff_blocks_ignores_prose_and_keeps_every_diff_fence():
    text = (
        "I'll update both files.\n"
        "\n"
        "```diff\n"
        "DIFF 1\n"
        "```\n"
        "\n"
        "Some text here\n"
        "```python\n"
        "not a diff\n"
        "```\n"
        "```diff\n"
        "DIFF 2\n"
        "```\n"
    )
    assert extract_diff_blocks(text) == ["DIFF 1\n", "DIFF 2\n"]


def test_extract_diff_blocks_accepts_indentation_and_spaced_language():
    assert extract_diff_blocks("  ```diff\nhello\n  ```") == ["hello\n"]
    assert extract_diff_blocks("``` diff\nhello\n```") == ["hello\n"]
    assert extract_diff_blocks("```diff\r\nhello\r\n```") == ["hello\n"]


def test_extract_diff_blocks_empty_and_absent():
    assert extract_diff_blocks("") == []
    assert extract_diff_blocks("no fences here") == []
    assert extract_diff_blocks("```diff\n```") == [""]
    assert extract_diff_blocks("```diff") == [""]


def test_parse_diff_with_code_fence():
    diff_text = """```diff
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
```"""
    result = parse_diff_per_file(diff_text)
    assert len(result) == 1
    file_path, patch = result[0]
    assert file_path == "file.txt"
    assert "+new" in patch
    assert "```" not in patch


def test_parse_diff_with_multiple_fences_and_prose():
    diff_text = """Don't apply the sketch below.
diff --git a/skip.txt b/skip.txt
--- a/skip.txt
+++ b/skip.txt
@@ -1 +1 @@
-no
+nope

```diff
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
```

And the other file:

```diff
diff --git a/other.txt b/other.txt
--- a/other.txt
+++ b/other.txt
@@ -1 +1 @@
-left
+right
```

Thanks!
"""
    result = parse_diff_per_file(diff_text)
    assert {path for path, _ in result} == {"file.txt", "other.txt"}
    patches = dict(result)
    assert "+new" in patches["file.txt"]
    assert "+right" in patches["other.txt"]
    assert "Don't apply" not in patches["file.txt"]
    assert "skip.txt" not in patches["file.txt"]


def test_parse_unterminated_diff_fence():
    diff_text = """```diff
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
    result = parse_diff_per_file(diff_text)
    assert len(result) == 1
    assert result[0][0] == "file.txt"
    assert "+new" in result[0][1]


def test_parse_raw_diff_without_fence_is_unchanged():
    diff_text = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
    result = parse_diff_per_file(diff_text)
    assert len(result) == 1
    assert result[0][0] == "file.txt"
    assert "+new" in result[0][1]


def test_apply_single_fenced_diff(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "file.txt"
    target.write_text("old\n")

    diff_text = """```diff
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
```"""
    assert apply_diff(str(project), diff_text) is True
    assert target.read_text() == "new\n"


def test_apply_multiple_fenced_diffs_surrounded_by_prose(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("old-a\n")
    (project / "b.txt").write_text("old-b\n")
    (project / "skip.txt").write_text("no\n")

    diff_text = """Here are the edits. Ignore this unfenced sketch:
diff --git a/skip.txt b/skip.txt
--- a/skip.txt
+++ b/skip.txt
@@ -1 +1 @@
-no
+nope

```diff
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old-a
+new-a
```

```python
print("not a diff")
```

Second file:

```diff
diff --git a/b.txt b/b.txt
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-old-b
+new-b
```
"""
    assert apply_diff(str(project), diff_text) is True
    assert (project / "a.txt").read_text() == "new-a\n"
    assert (project / "b.txt").read_text() == "new-b\n"
    assert (project / "skip.txt").read_text() == "no\n"


def test_non_diff_fence_is_not_extracted_or_applied(tmp_path):
    # Language must be exactly "diff". A python fence is left in the text, and
    # its closing fence is not a valid hunk line, so the file stays unchanged.
    project = tmp_path / "project"
    project.mkdir()
    target = project / "file.txt"
    target.write_text("old\n")

    wrapped = """```python
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
```"""
    assert extract_diff_blocks(wrapped) == []
    assert apply_diff(str(project), wrapped) is False
    assert target.read_text() == "old\n"
