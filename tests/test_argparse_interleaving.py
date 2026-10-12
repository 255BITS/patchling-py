"""--apply may sit before, between, or after filename arguments.

Re-implements the behavior from stale PR #5
(https://github.com/255BITS/patchling-py/pull/5) against the renamed package.
"""

import sys
from unittest import mock

import patchling.core as patchling_core


def parse(argv):
    with mock.patch.object(sys, "argv", argv):
        return patchling_core.parse_arguments()


def test_apply_after_file():
    args = parse(["patchling", "prompt", "file.js", "--apply"])
    assert args.apply is True
    assert args.files == ["file.js"]
    assert args.prompt == "prompt"


def test_apply_before_file():
    args = parse(["patchling", "prompt", "--apply", "file.js"])
    assert args.apply is True
    assert args.files == ["file.js"]


def test_apply_between_files():
    args = parse(["patchling", "prompt", "a.js", "--apply", "b.js"])
    assert args.apply is True
    assert args.files == ["a.js", "b.js"]


def test_apply_omitted_leaves_files_and_flag_clear():
    args = parse(["patchling", "prompt", "a.js", "b.js"])
    assert args.apply is False
    assert args.files == ["a.js", "b.js"]
