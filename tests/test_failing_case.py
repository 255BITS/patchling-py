import unittest
from patchling.core import smartapply

class TestSmartApplyEdgeCase(unittest.TestCase):
    def test_smartapply_think_tag_stripping(self):
        """
        This test verifies that if an LLM response includes extraneous wrapping (e.g.
        ), these tags are stripped from the final applied diff.
        """
        diff_text = '''diff --git a/hello.py b/hello.py
--- a/hello.py
++++ b/hello.py
@@ -1,2 +1,5 @@
 def hello():
     print('Hello')
++
++def goodbye():
++    print('Goodbye')'''
        original_files = {"hello.py": "def hello():\n    print('Hello')\n"}

        from unittest.mock import patch
        with patch('patchling.core.call_llm_for_apply', return_value="\ndef goodbye():\n    print('Goodbye')"):
            updated_files = smartapply(diff_text, original_files)

        self.assertIn("def goodbye():", updated_files.get("hello.py", ""))