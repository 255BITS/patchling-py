"""Opt-in per-file relevance filter.

Python counterpart of the JS library's ``filterRelevant``. For each candidate
file, a cheap OpenAI-compatible model is asked whether the file is relevant
to the task. Only ``yes`` files are returned for ``generate_diff`` and
``smartapply``.

Prompt version 3. Each request uses 1024 output tokens and
``reasoning_effort="low"`` (one retry without that parameter on a 4xx that
names it, or when the OpenAI client rejects the keyword). The cache key is
the SHA-256 of length-prefixed fields: prompt version, model, token limit,
the effort actually sent (``low``, or empty when the retry omitted it), task,
path, mode (``full``, ``truncated``, or ``binary``), the preview note, and
the exact body sent. A decision is a leading standalone yes/no after the
supported wrappers, including a quoted decision followed by a reason.
Anything else is malformed: the file is included, ``failed_open`` is set, and
the decision is not cached. Non-string message content is that same failure.
A cache read error is a miss and the model is called; a cache write error is
logged and does not abort the run. Invalid UTF-8 anywhere in a byte payload
is binary. ``on_binary="include"`` returns those original bytes unchanged.
A leading UTF-8 BOM (U+FEFF) stays in decoded text.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

# Default filter model. A pallets/flask pass reported recall 1.0 and about
# 0.8x the token overhead of the next model tried. Those figures are UNVERIFIED.
DEFAULT_FILTER_MODEL = "xiaomi/mimo-v2.6-flash"
DEFAULT_CONCURRENCY = 8
DEFAULT_MAX_BYTES = 200_000
DEFAULT_MAX_TOKENS = 12_000
# Shared with the JS filter: one completion budget for every model, plus
# reasoning_effort "low". A 4xx that names the parameter is retried once
# without it.
DEFAULT_MAX_OUTPUT_TOKENS = 1024
REASONING_EFFORT = "low"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF = 0.5
# Bump when the prompt or the yes/no contract changes so old cache rows miss.
PROMPT_VERSION = "3"
DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1/"

# Canonical skip list, shared with the JS filter. Case-insensitive.
# ``**`` crosses directories; ``*`` does not. Callers can replace this tuple,
# or pass an empty sequence to disable it.
DEFAULT_SKIP_PATTERNS = (
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/bun.lock",
    "**/bun.lockb",
    "**/npm-shrinkwrap.json",
    "**/Cargo.lock",
    "**/Gemfile.lock",
    "**/poetry.lock",
    "**/Pipfile.lock",
    "**/composer.lock",
    "**/go.sum",
    "**/uv.lock",
    "**/pdm.lock",
    "**/flake.lock",
    "**/pubspec.lock",
    "**/packages.lock.json",
    "**/Package.resolved",
    "**/*.min.js",
    "**/*.min.mjs",
    "**/*.min.css",
    "**/*.map",
    "**/dist/**",
    "**/build/**",
    "**/.next/**",
    "**/coverage/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.generated.*",
)

_log = logging.getLogger(__name__)


def _length_prefixed(fields: list[str]) -> bytes:
    """Canonical cache-key encoding: ``<utf-8 byte length>:<bytes>\\n`` per field."""
    chunks: list[bytes] = []
    for field in fields:
        raw = field.encode("utf-8")
        chunks.append(f"{len(raw)}:".encode("ascii"))
        chunks.append(raw)
        chunks.append(b"\n")
    return b"".join(chunks)

_POLICIES = ("include", "exclude", "truncate")

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_THINKING_BLOCK = re.compile(r"<thinking\b[^>]*>.*?</thinking>", re.IGNORECASE | re.DOTALL)
_THINKING_OPEN = re.compile(r"<thinking\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_ANSWER_PREFIX = re.compile(
    r"^(?:answer|decision)\s*[:\-]\s*",
    re.IGNORECASE,
)
_FENCE_LINE = re.compile(r"^```[a-zA-Z0-9_-]*\s*$")

_SYSTEM_PROMPT = (
    "You are a relevance filter for a code-editing task. "
    "Decide whether the given file must be read or edited to accomplish the task, "
    "or is necessary context for that edit. "
    'Reply with a single line that starts with exactly "yes" or exactly "no", '
    "followed by a short reason. "
    'Answer "yes" when the file is likely to need changes or is required context. '
    'Answer "no" when it is unrelated. Do not write a diff.'
)


@dataclass
class RelevanceDecision:
    """One file's relevance judgment.

    ``decision`` is ``"yes"`` (send the file on to generate_diff / smartapply)
    or ``"no"``. ``reason`` is a short explanation; ``raw`` is the model text
    before parsing, when a model was called. ``tokens`` is the total token
    count of that call (0 when no call was made). ``cached`` is true when the
    judgment was reused. ``skipped_reason`` is set when the file was not sent
    in full (``binary``, ``oversize``, ``truncated``, or ``ignored``).
    ``error`` is set when the call failed or the answer was empty or
    malformed; the file is still included. ``failed_open`` marks that case.
    """

    path: str
    decision: str
    reason: str = ""
    raw: Optional[str] = None
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    failed_open: bool = False
    cache_key: Optional[str] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("cache_key", None)
        return data


@dataclass
class FilterRelevantResult:
    """Filtered file map plus the per-file decisions, in input order."""

    files: dict
    decisions: list
    model: str
    errors: list = field(default_factory=list)

    def __post_init__(self):
        if not self.errors:
            self.errors = [d for d in self.decisions if d.error]

    @property
    def billed_prompt_tokens(self) -> int:
        return sum(d.prompt_tokens for d in self.decisions if not d.cached)

    @property
    def billed_completion_tokens(self) -> int:
        return sum(d.completion_tokens for d in self.decisions if not d.cached)

    @property
    def billed_tokens(self) -> int:
        return sum(d.tokens for d in self.decisions if not d.cached)

    @property
    def failed_open(self) -> list:
        """Files included because the call failed or the answer was empty or malformed."""
        return [d for d in self.decisions if d.failed_open]

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "files": list(self.files.keys()),
            "decisions": [d.to_dict() for d in self.decisions],
            "billed_tokens": self.billed_tokens,
            "billed_prompt_tokens": self.billed_prompt_tokens,
            "billed_completion_tokens": self.billed_completion_tokens,
            "errors": [d.to_dict() for d in self.errors],
            "failed_open": len(self.failed_open),
        }


class RelevanceCache:
    """In-memory relevance cache with an optional on-disk JSON file.

    The key is the SHA-256 of the exact classification inputs. Each field is
    written as ``<utf-8 byte length>:<bytes>`` and a newline, in this order:
    prompt version, model, output-token limit, the effort actually sent,
    task, file path, mode (``full``, ``truncated``, or ``binary``), the
    preview note (empty when there is none), and the body sent to the model.
    A full file and a truncated file that happen to show the same bytes do
    not share a key. Lookups try effort ``low`` first, then the empty effort
    used when that parameter was rejected. Fallback and error decisions are
    not stored. A disk read or write failure is logged and does not raise.
    """

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = os.fspath(path) if path else None
        self._entries: dict = {}
        self._lock = threading.Lock()
        if self.path and os.path.exists(self.path):
            self._load()

    @staticmethod
    def make_key(
        model: str,
        task: str,
        content: str,
        path: str,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        prompt_version: str = PROMPT_VERSION,
        effort: Optional[str] = REASONING_EFFORT,
        mode: str = "full",
        note: str = "",
    ) -> str:
        """Hash the exact inputs that produced a classification.

        ``content`` is the body sent to the model. ``effort`` is the
        ``reasoning_effort`` value actually sent, or an empty field when the
        parameter was omitted. ``mode`` is ``full``, ``truncated``, or
        ``binary``. ``note`` is the preview note, or empty.
        """
        fields = [
            str(prompt_version),
            str(model),
            str(max_output_tokens),
            "" if effort is None else str(effort),
            task,
            str(path),
            str(mode or "full"),
            "" if note is None else str(note),
            content,
        ]
        return hashlib.sha256(_length_prefixed(fields)).hexdigest()

    def get(self, key: str):
        with self._lock:
            record = self._entries.get(key)
            return dict(record) if record else None

    def put(self, key: str, record: dict) -> None:
        with self._lock:
            self._entries[key] = dict(record)

    def flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            payload = {"version": 1, "entries": self._entries}
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".relevance-cache-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                os.replace(tmp, self.path)
            except Exception as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                _log.warning("relevance cache write failed for %s: %s", self.path, exc)

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("relevance cache read failed for %s: %s", self.path, exc)
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, dict):
            self._entries = entries


def strip_think_tags(text: str) -> str:
    """Drop ``<think>`` / ``<thinking>`` blocks, including an unclosed tail."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _THINKING_BLOCK.sub("", cleaned)
    cleaned = _THINK_OPEN.sub("", cleaned)
    cleaned = _THINKING_OPEN.sub("", cleaned)
    cleaned = re.sub(r"</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</thinking>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


_WRAPPER_PAIRS = (
    ("**", "**"),
    ("__", "__"),
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),  # “ ”
    ("\u2018", "\u2019"),  # ‘ ’
)
_LEADING_YES_NO = re.compile(r"^(yes|no)(?=$|\s|[.,!:;)])", re.IGNORECASE)


def _peel_wrapping_pair(text: str) -> Optional[str]:
    for opener, closer in _WRAPPER_PAIRS:
        if (
            text.startswith(opener)
            and text.endswith(closer)
            and len(text) >= len(opener) + len(closer)
        ):
            return text[len(opener):-len(closer)]
    return None


def _strip_answer_wrappers(text: str) -> str:
    """Peel whitespace, Answer/Decision prefixes, quotes, bold, and backticks."""
    changed = True
    while changed and text:
        changed = False
        text = text.strip()
        match = _ANSWER_PREFIX.match(text)
        if match:
            text = text[match.end():]
            changed = True
            continue
        peeled = _peel_wrapping_pair(text)
        if peeled is not None:
            text = peeled
            changed = True
    return text.strip()


def _peel_first_token_wrapper(text: str) -> Optional[str]:
    """Drop one wrapper that encloses only the first token, keeping the rest."""
    for opener, closer in _WRAPPER_PAIRS:
        if not text.startswith(opener):
            continue
        rest = text[len(opener):]
        index = rest.find(closer)
        if index < 0:
            continue
        inner = rest[:index]
        if any(ch.isspace() for ch in inner):
            continue
        return inner + rest[index + len(closer):]
    return None


def _leading_yes_no(text: str) -> Optional[str]:
    """Yes or no when that exact word is the first token and then stops.

    The token must be followed by the end of the string, whitespace, or one
    of ``. , ! : ; )``. ``nope`` and ``yes?`` are malformed.
    """
    stripped = _strip_answer_wrappers(text)
    if not stripped:
        return None
    changed = True
    while changed:
        changed = False
        peeled = _peel_first_token_wrapper(stripped)
        if peeled is not None and peeled != stripped:
            stripped = peeled.lstrip()
            changed = True
    match = _LEADING_YES_NO.match(stripped)
    if not match:
        return None
    return match.group(1).lower()


def parse_relevance_answer(raw: str) -> tuple[Optional[str], str]:
    """Parse a yes/no relevance answer.

    Returns ``(decision, cleaned)``. ``decision`` is ``"yes"`` or ``"no"``
    only when the first standalone token, after the supported wrappers, is
    that word. Think tags are removed first, so a "no" inside a think tag
    cannot override a final "yes". A later "no" inside a reason does not
    count. Anything else is malformed (``None``); callers fail open and do
    not cache it.
    """
    from .core import swallow_reasoning

    text = strip_think_tags(raw or "")
    text, _reasoning = swallow_reasoning(text)
    text = text.strip()
    # Drop a wrapping code fence, if the whole answer is fenced.
    lines = text.splitlines()
    if len(lines) >= 2 and _FENCE_LINE.match(lines[0].strip()) and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    cleaned = text.strip()
    if not cleaned:
        return None, cleaned
    return _leading_yes_no(cleaned), cleaned


def _check_policy(name: str, value: str) -> str:
    if value not in _POLICIES:
        raise ValueError(f"{name} must be one of {', '.join(_POLICIES)}; got {value!r}")
    return value


def _resolve_model(model: Optional[str]) -> str:
    from .env import get_env

    return model or get_env("GPTDIFF_FILTER_MODEL") or DEFAULT_FILTER_MODEL


def output_token_budget(model: Optional[str], requested: int) -> int:
    """Completion budget for a relevance call. Every model uses the same cap."""
    del model
    return int(requested)


def reasoning_effort_for(model: Optional[str]) -> str:
    """Every relevance call asks for ``low`` effort. The model name is unused."""
    del model
    return REASONING_EFFORT


def _glob_to_re(pattern: str) -> re.Pattern:
    """Case-folded glob. ``**`` crosses directories; ``*`` does not.

    Same rules as the JS filter: a ``**/`` prefix may match nothing, and
    ``*`` never matches ``/``.
    """
    src = pattern.replace("\\", "/").lower()
    parts = []
    index = 0
    while index < len(src):
        if src.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if src.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        char = src[index]
        if char == "*":
            parts.append("[^/]*")
        else:
            if char in r"\^$+?.()|{}[]":
                parts.append("\\")
            parts.append(char)
        index += 1
    return re.compile("^" + "".join(parts) + "$")


def path_is_skipped(path: str, patterns) -> bool:
    """Match a skip glob against the full relative path, case-insensitively."""
    normalized = str(path).replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lower()
    for pattern in patterns:
        if _glob_to_re(pattern).match(normalized):
            return True
    return False


def is_transient_error(exc: BaseException) -> bool:
    """Timeouts, 5xx responses, and provider billing-reservation 503s."""
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timedout" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    if status_code is not None and status_code >= 500:
        return True
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return True
    if "billing reservation" in text:
        return True
    if any(token in text for token in (" 502", " 503", " 504", "status 502", "status 503", "status 504")):
        return True
    if text.startswith("502") or text.startswith("503") or text.startswith("504"):
        return True
    return False


def _reasoning_effort_rejected(exc: BaseException) -> bool:
    """The provider refused reasoning_effort. Retry once without it."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if "reasoning" not in text:
        return False
    if status is None:
        return "400" in text or "unsupported" in text or "invalid" in text
    try:
        return int(status) < 500
    except (TypeError, ValueError):
        return False


def _call_compatible(call, **kwargs):
    """Drop keyword arguments the injected client does not accept.

    Unit tests pass a narrow fake. The real ``call_llm`` accepts
    ``reasoning_effort``.
    """
    try:
        signature = inspect.signature(call)
    except (TypeError, ValueError):
        return call(**kwargs)
    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return call(**kwargs)
    accepted = {name: value for name, value in kwargs.items() if name in params}
    return call(**accepted)


def _resolve_cache(cache):
    if cache is False:
        return None
    if cache is None or cache is True:
        return RelevanceCache()
    if isinstance(cache, RelevanceCache):
        return cache
    if isinstance(cache, (str, os.PathLike)):
        return RelevanceCache(cache)
    raise TypeError("cache must be a RelevanceCache, a filesystem path, None, or False")


def count_tokens(text: str, encoder=None) -> int:
    """Shared token estimate: ``ceil(code points / 4)``.

    ``len`` counts Unicode code points, not UTF-8 bytes or UTF-16 units.
    Empty text is 0. ``encoder`` is ignored. The filter does not use tiktoken.
    """
    del encoder
    if not text:
        return 0
    return (len(text) + 3) // 4


def truncate_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 prefix of at most ``max_bytes``, cut on a code-point boundary.

    An incomplete trailing character is dropped. The result never contains
    U+FFFD.
    """
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    end = max_bytes
    start = end - 1
    while start >= 0 and (raw[start] & 0xC0) == 0x80:
        start -= 1
    if start < 0:
        return ""
    lead = raw[start]
    if lead < 0x80:
        need = 1
    elif lead < 0xE0:
        need = 2
    elif lead < 0xF0:
        need = 3
    elif lead < 0xF8:
        need = 4
    else:
        need = 1
    if start + need > end:
        end = start
    return raw[:end].decode("utf-8")


def _truncate_text(text: str, max_bytes: Optional[int], max_tokens: Optional[int], encoder) -> str:
    """Keep the first ``max_tokens * 4`` code points, then a UTF-8 byte cap.

    A Python ``str`` slice never splits a code point or a surrogate pair.
    ``truncate_utf8`` then drops an incomplete trailing character.
    """
    del encoder
    out = text
    if max_tokens and count_tokens(out) > max_tokens:
        out = out[: max_tokens * 4]
    if max_bytes:
        out = truncate_utf8(out, max_bytes)
    return out


def _utf8_text_or_none(sample: bytes) -> Optional[str]:
    """Decode ``sample``, allowing only an incomplete trailing sequence of <= 3 bytes.

    ``None`` means the sample is binary: a NUL, or a byte sequence that is not
    valid UTF-8 aside from that trailing cut.
    """
    if b"\x00" in sample:
        return None
    try:
        return sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        if "unexpected end of data" not in exc.reason:
            return None
        tail = len(sample) - exc.start
        if tail < 1 or tail > 3:
            return None
        try:
            return sample[: exc.start].decode("utf-8")
        except UnicodeDecodeError:
            return None


def is_binary_content(content) -> bool:
    """True when the payload is not text.

    The first 8192 bytes are decoded as UTF-8. An incomplete sequence of at
    most 3 bytes at that probe boundary is still text. A NUL or any other
    invalid byte inside the window is binary, and so is a non-empty window
    that decodes to empty text. Bytes past the window are checked too: any
    invalid UTF-8 there is binary. A leading BOM is text.
    """
    if isinstance(content, str):
        if "\x00" in content:
            return True
        raw = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
    else:
        return False
    return _bytes_are_binary(raw)


def _bytes_are_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    window = raw[:8192]
    text = _utf8_text_or_none(window)
    if text is None:
        return True
    if window and text == "":
        return True
    if _text_looks_binary(text):
        return True
    if len(raw) <= 8192:
        return False
    # The probe may end inside a character. The rest of the file has to be
    # well-formed UTF-8; an invalid byte after the window is not dropped.
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _text_looks_binary(sample: str) -> bool:
    if not sample:
        return False
    if "\x00" in sample:
        return True
    controls = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r\f")
    return controls / len(sample) > 0.30


def _coerce_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    raise TypeError(f"file content must be str or bytes, got {type(content).__name__}")


def _decode_utf8_text(raw: bytes) -> Optional[str]:
    """Decode UTF-8, keeping a leading BOM (U+FEFF).

    ``utf-8-sig`` is not used. A buffer of at most 8192 bytes may end on an
    incomplete sequence; those trailing bytes are dropped. A longer buffer
    must be entirely well-formed, so a bad byte after the probe is not cut off.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        if len(raw) > 8192:
            return None
        return _utf8_text_or_none(raw)


def _prepare_content(content) -> tuple[bool, str]:
    """Return ``(is_binary, text)``.

    Valid UTF-8 bytes are decoded to text, including a leading U+FEFF, and
    are never replaced with an empty string. Invalid UTF-8 anywhere, or a
    NUL byte, takes the binary path. Callers that include a binary file pass
    the original bytes through instead of this text.
    """
    if isinstance(content, str):
        return is_binary_content(content), content
    if isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
        if is_binary_content(raw):
            return True, ""
        text = _decode_utf8_text(raw)
        if text is None or (raw and text == ""):
            return True, ""
        return False, text
    raise TypeError(f"file content must be str or bytes, got {type(content).__name__}")


class _EnvelopeError(Exception):
    """The completion payload had no usable message content."""


def _usage_triplet(usage) -> tuple[int, int, int]:
    prompt = completion = total = 0
    if not usage:
        return 0, 0, 0
    try:
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total = usage.get("total_tokens") or (prompt + completion)
        else:
            prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0) or 0
            completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0) or 0
            total = getattr(usage, "total_tokens", None) or (prompt + completion)
        return int(prompt), int(completion), int(total)
    except (TypeError, ValueError):
        return 0, 0, 0


def _content_text(content):
    """Message content must already be a string.

    A list such as ``['no']`` is not joined or passed through ``str``. That
    would become the cacheable word ``no``. Non-string content fails open.
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        raise _EnvelopeError("message content is not text")
    return content


def _usage_and_text(response):
    """Read the first choice's message content.

    Missing choices, an empty choice list, a missing message or content
    field, and a non-JSON body raise :class:`_EnvelopeError`. Callers treat
    that as a failed-open decision and do not cache it.
    """
    payload = response
    if isinstance(response, (bytes, bytearray)):
        try:
            payload = bytes(response).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _EnvelopeError(f"non-JSON response: {exc}") from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _EnvelopeError(f"non-JSON response: {exc}") from exc

    if isinstance(payload, dict):
        if "choices" not in payload:
            raise _EnvelopeError("missing choices")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise _EnvelopeError("missing or empty choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict) or "content" not in message:
            raise _EnvelopeError("missing message content")
        content = _content_text(message.get("content"))
        prompt, completion, total = _usage_triplet(payload.get("usage") or {})
        return content, prompt, completion, total

    choices = getattr(payload, "choices", None)
    if choices is None:
        raise _EnvelopeError("missing choices")
    try:
        if len(choices) == 0:
            raise _EnvelopeError("missing or empty choices")
        message = choices[0].message
    except _EnvelopeError:
        raise
    except (AttributeError, IndexError, TypeError) as exc:
        raise _EnvelopeError("missing message content") from exc
    if message is None or not hasattr(message, "content"):
        raise _EnvelopeError("missing message content")
    content = _content_text(message.content)
    prompt, completion, total = _usage_triplet(getattr(payload, "usage", None))
    return content, prompt, completion, total


def _decision_from_record(path: str, record: dict, cached: bool) -> RelevanceDecision:
    return RelevanceDecision(
        path=path,
        decision=record["decision"],
        reason=record.get("reason") or "",
        raw=record.get("raw"),
        tokens=int(record.get("tokens") or 0),
        prompt_tokens=int(record.get("prompt_tokens") or 0),
        completion_tokens=int(record.get("completion_tokens") or 0),
        cached=cached,
        skipped_reason=record.get("skipped_reason"),
        error=record.get("error"),
        failed_open=bool(record.get("failed_open")),
        cache_key=record.get("cache_key"),
    )


def _skip_decision(path: str, decision: str, skipped_reason: str, reason: str) -> RelevanceDecision:
    return RelevanceDecision(
        path=path,
        decision=decision,
        reason=reason,
        raw=None,
        tokens=0,
        cached=False,
        skipped_reason=skipped_reason,
        error=None,
    )


def _build_user_prompt(task: str, paths, body: str, note: Optional[str]) -> str:
    if isinstance(paths, str):
        paths = [paths]
    label = "File path" if len(paths) == 1 else "File paths"
    parts = [f"Task:\n{task}", f"{label}:\n" + "\n".join(paths)]
    if note:
        parts.append(note)
    parts.append(f"File contents:\n{body}")
    parts.append("Is this file relevant to the task? yes/no")
    return "\n\n".join(parts)


def _complete(
    *,
    call,
    api_key,
    base_url,
    model,
    messages,
    max_output_tokens,
    reasoning_effort,
):
    return _call_compatible(
        call,
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_output_tokens,
        temperature=0.0,
        budget_tokens=None,
        reasoning_effort=reasoning_effort,
    )


def _ask(
    *,
    call,
    api_key,
    base_url,
    model,
    task,
    paths,
    body,
    note,
    max_output_tokens,
    reasoning_effort,
):
    user_prompt = _build_user_prompt(task, paths, body, note)
    user_content = user_prompt
    if model and "gemini" in model:
        user_content = _SYSTEM_PROMPT + "\n" + user_prompt
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    sent_effort = reasoning_effort
    try:
        response = _complete(
            call=call,
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=messages,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        if reasoning_effort and _reasoning_effort_rejected(exc):
            sent_effort = None
            response = _complete(
                call=call,
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                max_output_tokens=max_output_tokens,
                reasoning_effort=None,
            )
        else:
            raise
    raw, prompt_tokens, completion_tokens, total_tokens = _usage_and_text(response)
    # call_llm swallows a legacy SDK TypeError and retries with no effort.
    # That response records effort "" so the decision is not stored as "low".
    from .core import effort_sent_for

    recorded = effort_sent_for(response)
    if recorded is not None:
        sent_effort = recorded or None
    return raw, prompt_tokens, completion_tokens, total_tokens, sent_effort


def filter_relevant(
    files: dict,
    task: str,
    model: Optional[str] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    cache=None,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS,
    on_oversize: str = "truncate",
    on_binary: str = "exclude",
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    skip_patterns=None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    sleep: Optional[Callable[[float], None]] = None,
    call_llm: Optional[Callable] = None,
):
    """Ask a cheap model which files are relevant to ``task``.

    Parallel to JS ``filterRelevant(files, task, { model, concurrency, cache })``.
    ``files`` is a ``{path: content}`` map (str or bytes). Returns a
    :class:`FilterRelevantResult` whose ``files`` map contains only the ``yes``
    files, in input order, and whose ``decisions`` list has one
    :class:`RelevanceDecision` per input file.

    ``cache`` is a :class:`RelevanceCache`, a filesystem path (memory plus that
    file), ``None`` (a fresh in-memory cache for this call), or ``False``
    (disabled). The cache key hashes prompt version, model, output-token
    limit, the effort actually sent, the task, the file path, the mode, the
    preview note, and the exact body sent. Clear yes/no answers are cached
    under that effective key. Later lookups try effort ``low`` first, then
    the empty effort. Empty answers, malformed answers, and errors are not
    cached. A cache read error is a miss and the model is called; a cache
    write error is logged and ignored. A response with missing choices, an
    empty choice list, a missing message, non-string message content, or a
    non-JSON body fails that file open and is not cached. When the default
    client retries without ``reasoning_effort``, the decision is stored under
    the empty effort.

    Binary files and files over ``max_bytes`` / ``max_tokens`` are not sent in
    full. Invalid UTF-8 after the first 8192 bytes is still binary.
    ``on_binary="include"`` keeps the original bytes. ``on_binary`` and
    ``on_oversize`` are ``include`` (keep, no model
    call), ``exclude`` (drop, no model call), or ``truncate`` (ask the model
    about a truncated or redacted preview). Pass ``None`` for ``max_bytes`` or
    ``max_tokens`` to disable that limit. ``skip_patterns`` defaults to
    lockfiles and minified bundles; pass an empty sequence to disable that.
    Credentials resolve the same way as the rest of the library: explicit
    ``api_key`` / ``base_url``, then ``GPTDIFF_LLM_API_KEY`` and
    ``GPTDIFF_LLM_BASE_URL`` (including ``set_env`` overrides).
    """
    if not isinstance(files, dict):
        raise TypeError("files must be a dict of path -> content")
    if not isinstance(task, str) or task.strip() == "":
        raise ValueError("task is required")
    concurrency = int(concurrency)
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    on_oversize = _check_policy("on_oversize", on_oversize)
    on_binary = _check_policy("on_binary", on_binary)

    model = _resolve_model(model)
    output_budget = output_token_budget(model, max_output_tokens)
    effort = reasoning_effort_for(model)
    cache_obj = _resolve_cache(cache)
    patterns = DEFAULT_SKIP_PATTERNS if skip_patterns is None else tuple(skip_patterns)
    max_attempts = max(1, int(max_attempts))
    if sleep is None:
        sleep = time.sleep

    from .core import resolve_api_key, resolve_base_url

    api_key = resolve_api_key(api_key)
    base_url = resolve_base_url(base_url)

    if call_llm is None:
        from .core import call_llm as call_llm  # noqa: PLW0621

    by_path: dict[str, RelevanceDecision] = {}
    text_by_path: dict[str, str] = {}
    # Binary includes keep the caller's object. Decoding would replace or drop
    # the invalid byte and the bytes that follow it.
    binary_original: dict = {}
    # Group files that share a cache key so each distinct body is sent once.
    groups: dict[str, list] = {}
    group_order: list[str] = []

    for path, content in files.items():
        if path_is_skipped(path, patterns):
            by_path[path] = _skip_decision(
                path,
                "no",
                "ignored",
                "skipped lockfile or generated file",
            )
            continue
        binary, text = _prepare_content(content)
        if not binary:
            text_by_path[path] = text
        else:
            binary_original[path] = content
        if binary:
            if on_binary == "truncate":
                note = (
                    "Note: this file is binary. Its bytes are not shown; "
                    "judge relevance from the path and this note."
                )
                body = f"[binary file, {len(content) if isinstance(content, (bytes, bytearray)) else len(text.encode('utf-8'))} bytes, contents omitted]"
                item = _pending_item(
                    path, body, note, "truncated", "binary", task, model, output_budget, effort
                )
                _enqueue(groups, group_order, item)
            else:
                decision = "yes" if on_binary == "include" else "no"
                verb = "included" if decision == "yes" else "excluded"
                by_path[path] = _skip_decision(
                    path,
                    decision,
                    "binary",
                    f"binary file; {verb} without a model call",
                )
            continue

        n_bytes = len(text.encode("utf-8"))
        n_tokens = count_tokens(text)
        over_bytes = bool(max_bytes) and n_bytes > max_bytes
        over_tokens = bool(max_tokens) and n_tokens > max_tokens
        if over_bytes or over_tokens:
            if on_oversize == "truncate":
                body = _truncate_text(text, max_bytes, max_tokens, None)
                note = (
                    "Note: this file was truncated to fit the relevance check "
                    f"({n_bytes} bytes, {n_tokens} tokens). Judge from the portion shown."
                )
                item = _pending_item(
                    path, body, note, "truncated", "truncated", task, model, output_budget, effort
                )
                _enqueue(groups, group_order, item)
            else:
                decision = "yes" if on_oversize == "include" else "no"
                verb = "included" if decision == "yes" else "excluded"
                by_path[path] = _skip_decision(
                    path,
                    decision,
                    "oversize",
                    (
                        f"over size threshold ({n_bytes} bytes, {n_tokens} tokens); "
                        f"{verb} without a model call"
                    ),
                )
            continue

        item = _pending_item(path, text, None, None, "full", task, model, output_budget, effort)
        _enqueue(groups, group_order, item)

    def _safe_get(key: str):
        if cache_obj is None:
            return None
        try:
            return cache_obj.get(key)
        except Exception as exc:
            # A read failure is a cache miss. The model is called normally.
            _log.warning("relevance cache read failed for %s: %s", key, exc)
            return None

    def _reusable(hit) -> bool:
        if not hit or hit.get("decision") not in ("yes", "no"):
            return False
        if hit.get("error") or hit.get("failed_open"):
            return False
        reason = hit.get("reason") or ""
        if reason.startswith("malformed answer") or reason.startswith("error; included by default"):
            return False
        if reason.startswith("ambiguous answer"):
            return False
        return True

    def _lookup(sample):
        for key in (sample["key"], sample["key_omitted"]):
            hit = _safe_get(key)
            if _reusable(hit):
                return key, hit
        return None, None

    needs_call = False
    for key in group_order:
        if _lookup(groups[key][0])[0] is None:
            needs_call = True
            break
    if needs_call and not api_key:
        raise RuntimeError(
            "GPTDIFF_LLM_API_KEY is required for the relevance filter. "
            "Set it in the environment or pass api_key=."
        )

    def run_group(key: str):
        items = groups[key]

        def fail_open(message, raw=None, prompt_tokens=0, completion_tokens=0, total_tokens=0):
            # One decision per path. A fallback is never marked cached, so the
            # summary does not undercount files that were included by default.
            return [
                RelevanceDecision(
                    path=item["path"],
                    decision="yes",
                    reason=f"error; included by default: {message}",
                    raw=raw,
                    tokens=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached=False,
                    skipped_reason=item["skipped_reason"],
                    error=message,
                    failed_open=True,
                    cache_key=key,
                )
                for item in items
            ]

        sample = items[0]
        hit_key, hit = _lookup(sample)
        if hit is not None:
            return [
                _decision_from_record(item["path"], {**hit, "cache_key": hit_key}, cached=True)
                for item in items
            ]

        attempt = 0
        while True:
            try:
                raw, prompt_tokens, completion_tokens, total_tokens, sent_effort = _ask(
                    call=call_llm,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    task=task,
                    paths=[item["path"] for item in items],
                    body=sample["body"],
                    note=sample["note"],
                    max_output_tokens=output_budget,
                    reasoning_effort=effort,
                )
                break
            except _EnvelopeError as exc:
                return fail_open(f"malformed response: {exc}")
            except Exception as exc:
                if attempt >= max_attempts - 1 or not is_transient_error(exc):
                    return fail_open(f"{type(exc).__name__}: {exc}")
                sleep(retry_backoff * (2 ** attempt))
                attempt += 1

        parsed, cleaned = parse_relevance_answer(raw)
        # A reasoning model can consume the completion budget and leave no
        # visible answer. That is a failed-open error, not an ambiguous yes.
        if not (cleaned or "").strip():
            return fail_open(
                "empty answer",
                raw=raw,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        if parsed is None:
            # Malformed text is a fallback, same as an empty answer: include
            # the file, count it failed open, and do not cache it.
            return [
                RelevanceDecision(
                    path=item["path"],
                    decision="yes",
                    reason=f"malformed answer; included by default: {cleaned}",
                    raw=raw,
                    tokens=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached=False,
                    skipped_reason=item["skipped_reason"],
                    error="malformed answer",
                    failed_open=True,
                    cache_key=key,
                )
                for item in items
            ]
        decision = parsed
        reason = cleaned
        store_key = sample["key"] if sent_effort else sample["key_omitted"]
        record = {
            "decision": decision,
            "reason": reason,
            "raw": raw,
            "tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "skipped_reason": sample["skipped_reason"],
            "error": None,
            "failed_open": False,
            "cache_key": store_key,
        }
        if cache_obj is not None:
            try:
                cache_obj.put(store_key, record)
            except Exception as exc:
                _log.warning("relevance cache write failed for %s: %s", sample["path"], exc)
        return [
            RelevanceDecision(
                path=item["path"],
                decision=decision,
                reason=reason,
                raw=raw,
                tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached=False,
                skipped_reason=item["skipped_reason"],
                error=None,
                failed_open=False,
                cache_key=store_key,
            )
            for item in items
        ]

    try:
        if groups:
            workers = min(concurrency, len(groups))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(run_group, key): key for key in group_order}
                for future in as_completed(futures):
                    for decision in future.result():
                        by_path[decision.path] = decision
    finally:
        if cache_obj is not None:
            try:
                cache_obj.flush()
            except Exception as exc:
                _log.warning("relevance cache write failed: %s", exc)

    decisions = [by_path[path] for path in files]
    included = {}
    for path, content in files.items():
        decision = by_path[path]
        if decision.decision == "yes":
            if path in binary_original:
                included[path] = binary_original[path]
            elif path in text_by_path:
                included[path] = text_by_path[path]
            elif isinstance(content, str):
                included[path] = content
            else:
                included[path] = _coerce_text(content)

    return FilterRelevantResult(files=included, decisions=decisions, model=model)


def _pending_item(path, body, note, skipped_reason, mode, task, model, max_output_tokens, effort):
    note_text = "" if note is None else str(note)
    common = dict(
        model=model,
        task=task,
        content=body,
        path=path,
        max_output_tokens=max_output_tokens,
        prompt_version=PROMPT_VERSION,
        mode=mode,
        note=note_text,
    )
    return {
        "path": path,
        "body": body,
        "note": note,
        "skipped_reason": skipped_reason,
        "mode": mode,
        "key": RelevanceCache.make_key(**common, effort=effort),
        "key_omitted": RelevanceCache.make_key(**common, effort=""),
    }


def _enqueue(groups, group_order, item):
    key = item["key"]
    if key not in groups:
        groups[key] = []
        group_order.append(key)
    groups[key].append(item)


def format_filter_summary(result: FilterRelevantResult, verbose: bool = False) -> str:
    """One-screen summary of a filter pass. Per-file lines when ``verbose``."""
    total = len(result.decisions)
    kept = len(result.files)
    failed_open_count = len(result.failed_open)
    line = (
        f"Relevance filter ({result.model}): kept {kept}/{total} files, "
        f"{result.billed_tokens} tokens"
    )
    if failed_open_count:
        line += f", {failed_open_count} failed open"
    lines = [line]
    for decision in result.decisions:
        if decision.error or decision.failed_open:
            lines.append(
                f"  failed open {decision.path}: {decision.error or decision.reason} (included by default)"
            )
        elif verbose:
            flags = []
            if decision.cached:
                flags.append("cached")
            if decision.skipped_reason:
                flags.append(decision.skipped_reason)
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"  {decision.decision:3} {decision.path}{suffix}")
        elif decision.skipped_reason and decision.decision == "no":
            lines.append(f"  skip  {decision.path} ({decision.skipped_reason})")
    return "\n".join(lines)


def dump_decisions(result: FilterRelevantResult, path: os.PathLike | str) -> None:
    """Write the filter result as JSON so callers can inspect every decision."""
    destination = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(destination))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, indent=2)
        handle.write("\n")
