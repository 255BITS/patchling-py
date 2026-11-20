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
