from patchling import smartapply
from unittest.mock import patch

def test_smartapply_file_deletion():
    """Test that smartapply correctly handles file deletion diffs"""
    diff_text = '''diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def deprecated():
-    print("Remove me")'''

    original_files = {
        "old.py": "def deprecated():\n    print('Remove me')"
    }

    updated_files = smartapply(diff_text, original_files)
    
    # Verify deleted file is removed from dictionary
    assert "old.py" not in updated_files
    assert len(updated_files) == 0

    # Test applying deletion to non-existent file
    result = smartapply(diff_text, {})
    assert len(result) == 0

def test_smartapply_file_modification():
    """Test that smartapply correctly handles file modification diffs"""
    diff_text = '''diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,5 @@
 def hello():
     print('Hello')
+
+def goodbye():
+    print('Goodbye')'''

    original_hello = "def hello():\n    print('Hello')"
    original_files = {
        "hello.py": original_hello
    }

    # Mock LLM to return modified content
    with patch('patchling.core.call_llm_for_apply',
               return_value="\ndef goodbye():\n    print('Goodbye')"):

        updated_files = smartapply(diff_text, original_files)
        
        assert "hello.py" in updated_files
        assert original_hello != updated_files["hello.py"]

def test_smartapply_think_then_modify():
    """Test that smartapply correctly handles file modification diffs"""
    diff_text = '''diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,5 @@
 def hello():
     print('Hello')
+
+def goodbye():
+    print('Goodbye')'''

    original_files = {
        "hello.py": "def hello():\n    print('Hello')"
    }

    # Mock LLM to return modified content
    with patch('patchling.core.call_llm_for_apply',
               return_value="<think>Hello from thoughts</think>\ndef goodbye():\n    print('Goodbye')"):

        updated_files = smartapply(diff_text, original_files)
        
        assert "hello.py" in updated_files
        assert updated_files["hello.py"] == "def goodbye():\n    print('Goodbye')"


def test_smartapply_new_file_creation():
    """Test that smartapply handles new file creation through diffs"""
    diff_text = '''diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def new_func():
+    print('New function')'''

    original_files = {}

    # Mock LLM for new file creation
    with patch('patchling.core.call_llm_for_apply', return_value="def new_func():\n    print('New function')"):

        updated_files = smartapply(diff_text, original_files)
        
        assert "new.py" in updated_files
        assert updated_files["new.py"] == "def new_func():\n    print('New function')"


def test_smartapply_modify_nonexistent_file():
    """Test that smartapply handles modification diffs for non-existent files by creating them"""
    diff_text = '''diff --git a/newfile.py b/newfile.py
--- a/newfile.py
+++ b/newfile.py
@@ -0,0 +1,2 @@
++def new_func():
++    print('Created via diff')'''

    original_files = {}

    # Mock LLM to return content for new file
    with patch('patchling.core.call_llm_for_apply',
               return_value="def new_func():\n    print('Created via diff')"):

        updated_files = smartapply(diff_text, original_files)
        
        # Verify new file created with expected content
        assert "newfile.py" in updated_files
        assert updated_files["newfile.py"] == "def new_func():\n    print('Created via diff')"

        result = smartapply(diff_text, original_files)
        assert "newfile.py" in result

def test_smartapply_multi_file_modification(monkeypatch):
    """Test smartapply handles multi-file modifications through LLM integration.
    
    Verifies:
    - Correct processing of diffs affecting multiple files in single patch
    - Mocked LLM responses properly update each target file's content  
    - Non-targeted files remain unmodified
    
    Test Setup:
    - Original files include two target files and unrelated file  
    - Mock LLM to return modified content based on file path
    - Apply diff through LLM-powered smartapply
    
    Assertions:
    - Both target files show expected modifications
    - Unrelated file content remains unchanged"""
    diff_text = '''diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,2 @@
 def func1():
-    print("Old func1")
+    print("New func1")
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1,2 +1,2 @@
 def func2():
-    print("Old func2")
+    print("New func2")'''

    original_files = {
        "file1.py": "def func1():\n    print('Old func1')",
        "file2.py": "def func2():\n    print('Old func2')",
        "unrelated.py": "def unrelated():\n    pass"
    }

    # Mock LLM to return modified content based on file path
    def mock_call_llm(file_path, original_content, file_diff, model, api_key, base_url, extra_prompt=None, max_tokens=None):
        if file_path == "file1.py":
            return "def func1():\n    print('New func1')"
        elif file_path == "file2.py":
            return "def func2():\n    print('New func2')"
        return original_content

    monkeypatch.setattr('patchling.core.call_llm_for_apply', mock_call_llm)
    updated_files = smartapply(diff_text, original_files)
    
    # Verify both target files modified
    assert "file1.py" in updated_files
    assert "file2.py" in updated_files
    assert "unrelated.py" in updated_files
    
    # Check exact modifications
    assert "print('New func1')" in updated_files["file1.py"]
    assert "print('New func2')" in updated_files["file2.py"]
    
    # Verify unrelated file remains untouched
    assert updated_files["unrelated.py"] == "def unrelated():\n    pass"


def test_smartapply_complex_single_hunk(monkeypatch):
    """Test complex single hunk with multiple change types
    
    Validates proper handling of:
    - Line deletions (# Old processing logic, temp = data * 2)
    - Additions with new control flow (# Optimized pipeline, if not data check)
    - Context preservation (results list initialization)
    
    Setup:
    - Original function contains legacy processing logic
    - Diff removes temporary variable and adds guard clause
    - Mock LLM to return optimized version matching diff
    """
    diff_text = '''diff --git a/complex.py b/complex.py
--- a/complex.py
+++ b/complex.py
@@ -1,7 +1,8 @@
 def process(data):
-    # Old processing logic
-    temp = data * 2
+    # Optimized pipeline
+    if not data:
+        return []
     results = []
-    for x in temp:
+    for x in data:
         results.append(x ** 2)
     return results'''

    original_files = {
        "complex.py": (
            "def process(data):\n"
            "    # Old processing logic\n"
            "    temp = data * 2\n"
            "    results = []\n"
            "    for x in temp:\n"
            "        results.append(x ** 2)\n"
            "    return results"
        )
    }

    expected_content = (
        "def process(data):\n"
        "    # Optimized pipeline\n"
        "    if not data:\n"
        "        return []\n"
        "    results = []\n"
        "    for x in data:\n"
        "        results.append(x ** 2)\n"
        "    return results"
    )
    monkeypatch.setattr('patchling.core.call_llm_for_apply', lambda *args, **kwargs: expected_content)

    updated_files = smartapply(diff_text, original_files)
    
    assert "complex.py" in updated_files
    updated = updated_files["complex.py"]
    assert "Optimized pipeline" in updated
    assert "if not data:" in updated
    assert "temp = data * 2" not in updated
    assert "for x in data:" in updated

def test_smartapply_new_file_with_incorrect_header(monkeypatch):
    """Test that smartapply creates a new file from a diff with an incorrect '--- a/' header."""
    diff_text = """
diff --git a/game.js b/game.js
--- a/game.js
++++ b/game.js
@@ -0,0 +1,3 @@
+let player = {
+    class: "Warrior",
+};
"""
    original_files = {}
    expected_content = "let player = {\n    class: \"Warrior\",\n};"
    def mock_call_llm(file_path, original_content, file_diff, model, api_key, base_url, extra_prompt=None, max_tokens=None):
        return expected_content
    monkeypatch.setattr('patchling.core.call_llm_for_apply', mock_call_llm)
    updated_files = smartapply(diff_text, original_files)
    assert "game.js" in updated_files, "The new file 'game.js' should be created"
    assert updated_files["game.js"] == expected_content, "The file content should match the diff"