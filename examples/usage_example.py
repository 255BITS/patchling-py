#!/usr/bin/env python3
"""
Example usage of GPTDiff API with generate_diff and smartapply.
This example demonstrates how to transform a simple codebase using AI-generated diffs.
"""

from gptdiff import generate_diff, smartapply, build_environment
import os

# Set the GPTDiff API key
os.environ['GPTDIFF_LLM_API_KEY'] = 'your-api-key'

# Original codebase represented as a dictionary
files = {
    "main.py": "def old_name():\n    print('Need renaming')\n"
}

# Generate a transformation diff to rename function from old_name to new_name and update its call sites
diff_text = generate_diff(
    environment=build_environment(files),
    goal='Rename function to new_name(): and update its call sites accordingly',
)

# Apply the AI-generated diff with smartapply, ensuring safe modifications
updated_files = smartapply(diff_text, files)

# Output the transformed codebase
print("Transformed codebase:")
print(updated_files.get("main.py", "File not found"))