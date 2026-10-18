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
    DEFAULT_CONCURRENCY as DEFAULT_FILTER_CONCURRENCY,
    DEFAULT_FILTER_MODEL,
    DEFAULT_MAX_BYTES as DEFAULT_FILTER_MAX_BYTES,
    DEFAULT_MAX_OUTPUT_TOKENS as DEFAULT_FILTER_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_TOKENS as DEFAULT_FILTER_MAX_TOKENS,
    DEFAULT_SKIP_PATTERNS,
    PROMPT_VERSION,
    REASONING_EFFORT as DEFAULT_FILTER_REASONING_EFFORT,
    FilterRelevantResult,
    RelevanceCache,
    RelevanceDecision,
    count_tokens,
    dump_decisions,
    filter_relevant,
    format_filter_summary,
    is_binary_content,
    parse_relevance_answer,
    path_is_skipped,
)

# Same value as PROMPT_VERSION, under the second name the JS package exports.
FILTER_PROMPT_VERSION = PROMPT_VERSION

__version__ = "0.9.3"

__all__ = [
    "__version__",
    "DEFAULT_BASE_URL",
    "DEFAULT_FILTER_CONCURRENCY",
    "DEFAULT_FILTER_MAX_BYTES",
    "DEFAULT_FILTER_MAX_OUTPUT_TOKENS",
    "DEFAULT_FILTER_MAX_TOKENS",
    "DEFAULT_FILTER_MODEL",
    "DEFAULT_FILTER_REASONING_EFFORT",
    "DEFAULT_MODEL",
    "DEFAULT_SKIP_PATTERNS",
    "FILTER_PROMPT_VERSION",
    "PROMPT_VERSION",
    "FilterRelevantResult",
    "RelevanceCache",
    "RelevanceDecision",
    "SmartApplyError",
    "apply_diff",
    "build_environment",
    "call_llm",
    "call_llm_for_diff",
    "color_code_diff",
    "count_tokens",
    "dump_decisions",
    "extract_diff_blocks",
    "filter_relevant",
    "format_filter_summary",
    "generate_diff",
    "get_env",
    "is_binary_content",
    "load_project_files",
    "parse_diff_per_file",
    "parse_relevance_answer",
    "path_is_skipped",
    "resolve_api_key",
    "resolve_base_url",
    "save_files",
    "set_env",
    "smartapply",
    "strip_bad_output",
    "strip_think_tags",
    "swallow_reasoning",
]
