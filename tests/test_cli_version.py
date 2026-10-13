"""Installed entry points report their version without required arguments."""

from pathlib import Path
import subprocess
import sys

import pytest

import patchling


@pytest.mark.parametrize("command", ["patchling", "patchling-apply", "gptdiff", "gptpatch"])
def test_installed_cli_version(command, tmp_path):
    executable = Path(sys.executable).with_name(command)
    result = subprocess.run(
        [str(executable), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{command} {patchling.__version__}"
    assert result.stderr == ""
