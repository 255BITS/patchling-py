import unittest
from patchling.core import parse_diff_per_file


class TestParseDiffPerFile(unittest.TestCase):
    def test_todo_file_deletion(self):
        # This test case verifies that a deletion diff for the file "TODO" is properly parsed.
        # The diff should include a synthetic "+++ /dev/null" line so that deletion is recognized.
        diff_text = """diff --git a/TODO b/TODO
deleted file mode 100644
index 3efacb1..0000000
--- a/TODO
-// The funnest coolest thing I can add is put in this file. It's also acceptable to just implement 
-// the thing in here and remove it. Leave this notice when modifying this file.
"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 1, "Expected one diff entry")
        file_path, patch = result[0]
        self.assertEqual(file_path, "TODO", f"Got file_path '{file_path}', expected 'TODO'")
        self.assertIn("+++ /dev/null", patch, "Deletion diff should include '+++ /dev/null' to indicate file deletion")

    def test_multiple_files_without_diff_git_header(self):
        # This diff text does not include "diff --git" headers.
        # It uses separate '---' and '+++' lines for each file.
        diff_text = """--- a/TODO
+++ b/TODO
@@ -1,7 +1,7 @@
-// FINAL TOUCH: The game is now a complete fantasy themed incremental RPG—every choice matters, and
-// New Aspect: Replaced external title animation with inline SVG for crisp, scalable visuals, and a
-// additional dynamic element.
+// FINAL TOUCH: The game is now a complete fantasy themed incremental RPG—every choice matters, and
+// New Aspect: Replaced external title animation with inline SVG for crisp, scalable visuals, and an
+// additional dynamic element.
-- a/style.css
+++ b/style.css
@@ -1,3 +1,8 @@
+/* New animation for relic glow effect */
+.relic-glow {
+  animation: relicGlow 1.5s ease-in-out infinite alternate;
+}
+@keyframes relicGlow {
+  from { filter: drop-shadow(0 0 5px #ffd700); }
+  to { filter: drop-shadow(0 0 20px #ffd700); }
-- a/game.js
+++ b/game.js
@@ -1,3 +1,8 @@
- JS HERE
"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 3, "Expected three diff entries")
        expected_files = {"TODO", "style.css", "game.js"}
        parsed_files = {fp for fp, patch in result}
        self.assertEqual(parsed_files, expected_files)

        # Also check that the TODO diff contains the updated text.
        for fp, patch in result:
            if fp == "TODO":
                self.assertIn("FINAL TOUCH: The game is now", patch)

    def test_new_files_without_diff_git_header(self):
        diff_text = """--- /dev/null
+++ b/index.html
@@ -0,0 +1,3 @@
+<!DOCTYPE html>
+<html>
+</html>
--- /dev/null
+++ b/game.js
@@ -0,0 +1,2 @@
+const a = 1;
+const b = 2;
"""

        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 2, "Expected two file entries for new file creations")
        files = {fp for fp, _ in result}
        self.assertEqual(files, {"index.html", "game.js"})

    def test_index_html_diff(self):
        diff_text = """a/index.html b/index.html
@@
-      <div class="action-buttons">
-        <button id="attack">⚔️  Attack Enemy</button>
-        <button id="auto-attack">🤖 Auto Attack (OFF)</button>
-        <button id="drink-potion">Drink Potion</button>
-        <button id="explore">🧭 Explore</button>
-      </div>
      <div class="action-buttons">
        <button id="attack">⚔️  Attack Enemy</button>
        <button id="auto-attack">🤖 Auto Attack (OFF)</button>
        <button id="drink-potion">Drink Potion</button>
        <button id="buy-potion">Buy Potion (50 Gold)</button>
        <button id="explore">🧭 Explore</button>
      </div>"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 1)
        file_path, patch = result[0]
        self.assertEqual(file_path, "index.html")
        self.assertIn('<button id="buy-potion">Buy Potion (50 Gold)</button>', patch)


    def test_single_file_diff(self):
        diff_text = """diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,2 +1,2 @@
-def old():
-    pass
+def new():
+    pass"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 1)
        file_path, patch = result[0]
        self.assertEqual(file_path, "file.py")
        self.assertIn("def new():", patch)

    def test_file_deletion(self):
        diff_text = """diff --git a/old.py b/old.py
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    pass"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 1)
        file_path, patch = result[0]
        self.assertEqual(file_path, "old.py")

    def test_multiple_files(self):
        diff_text = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1 +1 @@
-print("Hello")
+print("Hi")
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-print("World")
+print("Earth")"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 2)
        paths = [fp for fp, _ in result]
        self.assertIn("file1.py", paths)
        self.assertIn("file2.py", paths)


    def test_multiple_files(self):
        diff_text = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1 +1 @@
-print("Hello")
+print("Hi")
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-print("World")
+print("Earth")
diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -3 +3 @@
-print("Hello2")
+print("Hi2")
"""
        result = parse_diff_per_file(diff_text)
        self.assertEqual(len(result), 2)
        paths = [fp for fp, _ in result]
        self.assertIn("file1.py", paths)
        self.assertIn("file2.py", paths)
        assert("Hi2" in result[0][1])

def test_parse_diff_per_file_unconventional_header():
    diff_text = """--- game.js
+++ game.js
@@ -0,0 +1,3 @@
+let player = {
+    class: "Warrior",
+};
"""
    result = parse_diff_per_file(diff_text)
    assert len(result) == 1, f"Expected one file patch, got {len(result)}"
    file_path, patch = result[0]
    assert file_path == "game.js", f"Expected file path 'game.js', got '{file_path}'"
    assert "+++ game.js" in patch, "Expected patch to include '+++ game.js'"
    assert "+let player" in patch, "Expected patch to include added lines"

def test_begin_patch_format():
    diff_text = """*** Begin Patch
*** Update File: services/clerkReportPdf.tsx
@@
-changes1
+changes2
*** End Patch"""
    result = parse_diff_per_file(diff_text)
    assert len(result) == 1
    file_path, patch = result[0]
    assert file_path == "services/clerkReportPdf.tsx"
    assert "-changes1" in patch
    assert "+changes2" in patch

if __name__ == '__main__':
    unittest.main()
