"""The default diff model must be a live NanoGPT id (no network here)."""
import patchling
from patchling import core
from patchling.env import DEFAULT_MODEL

NEW_DEFAULT = "xiaomi/mimo-v2.6-pro"
# Retired on NanoGPT (HTTP 410) / never listed there; neither may be the default.
RETIRED = ("xiaomi/mimo-v2.5-pro-ultraspeed", "deepseek-reasoner")


def test_default_model_is_mimo_v2_6_pro():
    assert DEFAULT_MODEL == NEW_DEFAULT
    assert patchling.DEFAULT_MODEL == NEW_DEFAULT


def test_retired_ids_are_not_the_default():
    for retired in RETIRED:
        assert DEFAULT_MODEL != retired


def test_unset_env_resolves_to_new_default(monkeypatch):
    monkeypatch.delenv("GPTDIFF_MODEL", raising=False)
    patchling.set_env("GPTDIFF_MODEL", None)
    assert core.resolve_model(None) == NEW_DEFAULT
