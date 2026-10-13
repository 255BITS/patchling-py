"""Natural-language diffs and resilient patch application.

Public names mirror 255BITS/patchling (the JavaScript package) in snake_case.
Existing names such as ``generate_diff`` and ``smartapply`` stay in place.
"""

from .applydiff import apply_diff, extract_diff_blocks, parse_diff_per_file
from .core import (
    SmartApplyError,
    build_environment,
    call_llm,
    call_llm_for_diff,
    color_code_diff,
    generate_diff,
    load_project_files,
    resolve_api_key,
    resolve_base_url,
    save_files,
    smartapply,
    strip_bad_output,
    strip_think_tags,
    swallow_reasoning,
)
from .env import DEFAULT_BASE_URL, DEFAULT_MODEL, get_env, set_env
from .relevance import (
    FilterRelevantResult,
    RelevanceCache,
    RelevanceDecision,
    filter_relevant,
)

__version__ = "0.9.1"

__all__ = [
    "__version__",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "FilterRelevantResult",
    "RelevanceCache",
    "RelevanceDecision",
    "SmartApplyError",
    "apply_diff",
    "build_environment",
    "call_llm",
    "call_llm_for_diff",
    "color_code_diff",
    "extract_diff_blocks",
    "filter_relevant",
    "generate_diff",
    "get_env",
    "load_project_files",
    "parse_diff_per_file",
    "resolve_api_key",
    "resolve_base_url",
    "save_files",
    "set_env",
    "smartapply",
    "strip_bad_output",
    "strip_think_tags",
    "swallow_reasoning",
]
