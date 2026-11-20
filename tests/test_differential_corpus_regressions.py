"""Regression tests for the JS/Py differential corpus (patchling loop).

Fixtures are the exact cases from the JS repo's benchmark corpus
(255BITS/patchling, benchmark/diff-corpus.json) where the Python engine
diverged from the JS reference implementation. Each test names the
divergence it pins (P1-P5 in the loop's Pass 0 recon).
"""

from patchling.applydiff import apply_patch_to_content, parse_diff_per_file


def _apply_single(original, diff):
    patches = parse_diff_per_file(diff)
    assert len(patches) == 1
    path, patch = patches[0]
    return path, apply_patch_to_content(original, patch)


def test_p3_no_trailing_newline_delete_does_not_duplicate_last_line():
    # P3: git's "last line loses its newline" pattern written with the kept
    # line as context plus a restating add. The @@ new-line count confirms
    # the collapsed reading, so the add must be dropped, not duplicated.
    original = 'package cfg\n\nconst Timeout = 30\nconst Retries = 3\nconst Debug = true'
    diff = '--- a/const.go\n+++ b/const.go\n@@ -2,4 +2,3 @@\n \n const Timeout = 30\n const Retries = 3\n-const Debug = true\n\\ No newline at end of file\n+const Retries = 3\n\\ No newline at end of file'
    # The corpus's expected text omits the final newline; the JS reference
    # implementation emits it (both are scored equal by the benchmark's
    # trailing-newline normalization). The pin here is byte-parity with JS.
    expected = 'package cfg\n\nconst Timeout = 30\nconst Retries = 3\n'
    path, applied = _apply_single(original, diff)
    assert path == 'const.go'
    assert applied == expected


def test_p2_markdown_list_deletion_keeps_whole_patch():
    # P2 (silent truncation): the deletion of a markdown list item is the
    # body line `-- [ ] Write applier`, which the old parser mistook for a
    # file header and truncated the patch at that line. The deterministic
    # applier then returned the (nearly) original file as if the edit had
    # applied. The patch must stay whole and the edit must land exactly.
    original = '# TODO\n\n- [x] Write parser\n- [ ] Write applier'
    diff = '--- a/TODO.md\n+++ b/TODO.md\n@@ -1,4 +1,6 @@\n # TODO\n \n - [x] Write parser\n-- [ ] Write applier\n+- [x] Write applier\n+- [ ] Write tests\n+- [ ] Publish to npm\n'
    expected = '# TODO\n\n- [x] Write parser\n- [x] Write applier\n- [ ] Write tests\n- [ ] Publish to npm\n'
    patches = parse_diff_per_file(diff)
    assert [p for p, _ in patches] == ['TODO.md']
    assert "- [ ] Write applier" in patches[0][1]
    assert apply_patch_to_content(original, patches[0][1]) == expected


def test_p5_double_minus_body_line_falls_back():
    # P5: a `--` body line is a deletion of a line whose content starts
    # with `-`; the file has no such line, so the deterministic applier
    # must refuse (None -> LLM fallback), not return a wrong apply. The
    # old parser split the patch at that line and applied the head alone.
    original = 'const config = {\n  debug: true,\n  retries: 3,\n  timeout: 5000,\n};\n\nexport default config;\n'
    diff = '--- a/src/config.js\n+++ b/src/config.js\n@@ -1,5 +1,4 @@\n const config = {\n--  debug: true,\n   retries: 3,\n   timeout: 5000,\n };\n'
    patches = parse_diff_per_file(diff)
    assert [p for p, _ in patches] == ['src/config.js']
    assert "--  debug: true," in patches[0][1]
    assert apply_patch_to_content(original, patches[0][1]) is None
