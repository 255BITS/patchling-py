"""Version 0.8.1 is single-sourced from patchling.__version__."""

from importlib.metadata import version
from pathlib import Path

import patchling


ROOT = Path(__file__).resolve().parents[1]


def test_version_constant_is_0_8_1():
    assert patchling.__version__ == "0.8.1"
    assert version("patchling") == "0.8.1"


def test_pyproject_does_not_hardcode_a_second_version():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert 'version = "0.8.1"' not in text
    assert 'attr = "patchling.__version__"' in text


def test_bumpversion_tracks_the_package_constant():
    cfg = (ROOT / ".bumpversion.cfg").read_text()
    assert "current_version = 0.8.1" in cfg
    assert "patchling/__init__.py" in cfg
    assert "setup.py" not in cfg


def test_setup_py_is_only_a_shim():
    text = (ROOT / "setup.py").read_text()
    assert "pyproject.toml" in text
    assert "version" not in text
