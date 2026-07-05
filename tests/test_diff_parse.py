import pytest
from patchling.core import color_code_diff

def test_color_code_diff():
    """
    Test that lines starting with '-' are wrapped in red color codes
    and lines starting with '+' are wrapped in green color codes.
    """
    diff_text = """-this line is removed
+this line is added
 unchanged line
-removed again
+and added again
 some other neutral line"""

    colorized = color_code_diff(diff_text)

    # We expect lines beginning with '-' to be in red
    assert "\033[31m-this line is removed\033[0m" in colorized
    assert "\033[31m-removed again\033[0m" in colorized

    # We expect lines beginning with '+' to be in green
    assert "\033[32m+this line is added\033[0m" in colorized
    assert "\033[32m+and added again\033[0m" in colorized

    # Lines unchanged should remain uncolored
    assert "unchanged line" in colorized
    assert "some other neutral line" in colorized

    # Ensure no erroneous color codes are added
    # by counting them in the final output
    color_code_count = colorized.count('\033[')
    # We have four lines that should be color-coded, so we expect 4 * 2 = 8 color code inserts
    # (start code + reset code per line)
    assert color_code_count == 8
