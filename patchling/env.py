"""Environment access shared by the library and the CLI.

Overrides registered with ``set_env`` are consulted before the process
environment, matching ``getEnv`` / ``setEnv`` in the JS package
(255BITS/patchling, ``src/env.js``).
"""

from __future__ import annotations

import os

# Historical Python default. The JS package currently advertises a different
# model string; this constant is the one ``generate_diff`` and ``smartapply``
# use when ``GPTDIFF_MODEL`` is unset, so existing callers keep their model.
DEFAULT_MODEL: str = "deepseek-reasoner"
DEFAULT_BASE_URL: str = "https://nano-gpt.com/api/v1/"

_overrides: dict[str, str] = {}


def set_env(name: str, value: str | None) -> None:
    """Register an environment override. Pass None to clear it."""
    if value is None:
        _overrides.pop(name, None)
    else:
        _overrides[name] = value


def get_env(name: str, fallback: str | None = None) -> str | None:
    """Read ``name`` from overrides, then the process environment, then ``fallback``."""
    if name in _overrides:
        return _overrides[name]
    if name in os.environ:
        return os.environ[name]
    return fallback
