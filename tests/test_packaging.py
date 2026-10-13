"""Package and release metadata share patchling.__version__."""

from configparser import ConfigParser
from importlib.metadata import version
from pathlib import Path

import patchling


ROOT = Path(__file__).resolve().parents[1]


def test_installed_version_matches_package():
    assert version("patchling") == patchling.__version__


def test_pyproject_does_not_hardcode_a_second_version():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert f'version = "{patchling.__version__}"' not in text
    assert 'attr = "patchling.__version__"' in text


def test_bumpversion_tracks_the_package_constant():
    cfg = (ROOT / ".bumpversion.cfg").read_text()
    parser = ConfigParser(interpolation=None)
    parser.read_string(cfg)
    assert parser["bumpversion"]["current_version"] == patchling.__version__
    assert "patchling/__init__.py" in cfg
    assert "setup.py" not in cfg


def test_setup_py_is_only_a_shim():
    text = (ROOT / "setup.py").read_text()
    assert "pyproject.toml" in text
    assert "version" not in text
