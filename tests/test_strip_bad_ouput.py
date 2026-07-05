# File: tests/test_strip_bad_output.py
import pytest
from patchling.core import strip_bad_output


def test_strip_bad_output_removes_wrapping():
    """
    LLM wraps the file in a ```diff fence with preamble and postamble.
    We should extract the file content verbatim, including its trailing newline.
    """
    original = "def hello():\n    print('Hello')\n"
    updated = (
        "This is the file you requested:\n"
        "```diff\n"
        "def hello():\n"
        "    print('Goodbye')\n"
        "```\n"
        "Thank you!"
    )
    expected = "def hello():\n    print('Goodbye')\n"
    result = strip_bad_output(updated, original)
    assert result == expected, f"Expected:\n{expected!r}\nGot:\n{result!r}"


def test_strip_bad_output_no_change_when_original_has_code_block():
    """
    If the original file already starts with a code fence, return updated as-is.
    """
    original = "```diff\ndef hello():\n    print('Hello')\n```"
    updated = "```diff\ndef hello():\n    print('Modified')\n```"
    result = strip_bad_output(updated, original)
    assert result == updated


def test_strip_bad_output_no_wrapping_detected():
    """
    If the updated output has no code fence, return it unchanged
    (preserving the trailing newline).
    """
    original = "def hello():\n    print('Hello')\n"
    updated = "def hello():\n    print('Modified')\n"
    result = strip_bad_output(updated, original)
    assert result == updated


def test_strip_bad_output_prod_case():
    """
    Updated output has preamble, a typescript language tag, and postamble.
    Extracted content keeps its trailing newline.
    """
    original = "def foo():\n    pass\n"
    updated = (
        "Here's the entire file after applying the diff:\n\n"
        "```typescript\n"
        "def foo():\n"
        "    print('Modified')\n"
        "```\n"
        "Some trailing text that should be ignored."
    )
    expected = "def foo():\n    print('Modified')\n"
    result = strip_bad_output(updated, original)
    assert result == expected, f"Expected:\n{expected!r}\nGot:\n{result!r}"


def test_strip_bad_output_preserves_inner_code_fences():
    """
    Regression: a Markdown file containing triple-backtick code blocks must
    NOT be truncated at the first inner fence when the LLM wraps the whole
    file in an outer fence. Previously the greedy-but-non-greedy regex
    `re.search(r"```(.*?)```", ...)` matched the outer opener against the
    first inner closer, losing everything after.
    """
    original = (
        "# README\n"
        "\n"
        "Some code:\n"
        "\n"
        "```python\n"
        "print(\"hi\")\n"
        "```\n"
        "\n"
        "More text at end.\n"
    )
    updated = "```markdown\n" + original + "```"
    result = strip_bad_output(updated, original)
    assert result == original, (
        f"Inner code fences truncated content.\n"
        f"Expected:\n{original!r}\nGot:\n{result!r}"
    )


def test_strip_bad_output_preserves_trailing_newline_plain():
    """
    Regression: even for a plain file with no wrapping, the trailing newline
    must be preserved. Previously `.strip()` ate it on every smartapply.
    """
    original = "def hello():\n    print('hi')\n"
    updated = "def hello():\n    print('bye')\n"
    result = strip_bad_output(updated, original)
    assert result.endswith("\n"), f"Trailing newline lost: {result!r}"
    assert result == updated


def test_strip_bad_output_drops_stray_language_after_bare_fence():
    """
    If the LLM opens a bare ``` and puts the language on the next line,
    drop that stray language tag.
    """
    original = "def hello():\n    pass\n"
    updated = "```\npython\ndef hello():\n    print('hi')\n```\n"
    expected = "def hello():\n    print('hi')\n"
    result = strip_bad_output(updated, original)
    assert result == expected, f"Expected:\n{expected!r}\nGot:\n{result!r}"


def test_strip_bad_output_unclosed_fence_returns_input():
    """
    If we can find an opening fence but no matching closing fence, return
    the input unchanged rather than guessing.
    """
    original = "def hello():\n    pass\n"
    updated = "```python\ndef hello():\n    print('hi')\n"
    result = strip_bad_output(updated, original)
    assert result == updated
