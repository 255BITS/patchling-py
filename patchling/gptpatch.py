#!/usr/bin/env python3
"""
Command line tool to apply a unified diff directly to a file system.

Usage:
    gptpatch --diff "<diff text>"
    or
    gptpatch path/to/diff.patch

This tool uses the same patch-application logic as gptdiff.
"""

import sys
import argparse
from pathlib import Path
from patchling.core import apply_diff, smart_apply_patch, color_code_diff

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Apply a unified diff to the file system using GPTDiff's patch logic."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--diff",
        type=str,
        help="Unified diff text to apply (provide as a string)."
    )
    group.add_argument(
        "diff_file",
        nargs="?",
        help="Path to a file containing the unified diff."
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=".",
        help="Project directory where the diff should be applied (default: current directory)."
    )
    parser.add_argument('--nobeep', action='store_false', dest='beep', default=True, help='Disable completion notification beep')
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model to use for applying the diff"
    )
    parser.add_argument(
        "--max_tokens",
        type=int,
        default=30000,
        help="Maximum tokens to use for LLM responses"
    )
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output with detailed information')
    parser.add_argument('--dumb', action='store_true', default=False, help='Attempt dumb apply before trying smart apply')
    return parser.parse_args()

def main():
    args = parse_arguments()
    import patchling.core as gd
    gd.VERBOSE = args.verbose
    if args.diff:
        diff_text = args.diff
    else:
        diff_path = Path(args.diff_file)
        if not diff_path.exists():
            print(f"Error: Diff file '{args.diff_file}' does not exist.")
            sys.exit(1)
        diff_text = diff_path.read_text(encoding="utf8")

    project_dir = args.project_dir

    if args.verbose:
        print("\n\033[1;34mDiff to be applied:\033[0m")
        print(color_code_diff(diff_text))
        print("")

    if args.dumb:
        success = apply_diff(project_dir, diff_text)
        if success:
            print("\033[1;32m✅ Diff applied successfully.\033[0m")
        else:
            print("\033[1;31m❌ Failed to apply diff using git apply. Attempting smart apply.\033[0m")
            smart_apply_patch(project_dir, diff_text, "", args)
    else:
        smart_apply_patch(project_dir, diff_text, "", args)
        
if __name__ == "__main__":
    main()
