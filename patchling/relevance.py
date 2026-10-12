"""Opt-in per-file relevance filter.

Python counterpart of the JS library's ``filterRelevant``. For each candidate
file, a cheap OpenAI-compatible model is asked whether the file is relevant
to the task. Only ``yes`` files are returned for ``generate_diff`` and
``smartapply``.

A non-empty ambiguous answer includes the file and is not cached. An empty
answer is an error, not an ambiguous yes: the file is included (failed open),
the decision is not cached, and the summary counts it. Transient call errors
are retried before that. Lockfiles and generated bundles are skipped.
"""

from __future__ import annotations

import fnmatch
import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

# xiaomi/mimo-v2.6-flash had perfect recall and the best precision on a
# pallets/flask labeling pass. gemini-2.5-flash-lite kept several times as
# many files; gpt-4.1-nano missed relevant ones.
DEFAULT_FILTER_MODEL = "xiaomi/mimo-v2.6-flash"
DEFAULT_CONCURRENCY = 8
DEFAULT_MAX_BYTES = 200_000
DEFAULT_MAX_TOKENS = 12_000
DEFAULT_MAX_OUTPUT_TOKENS = 256
# Reasoning models can spend a small completion budget on hidden reasoning and
# return an empty answer. Give them room for a yes/no after that, and ask the
# provider not to reason.
REASONING_MAX_OUTPUT_TOKENS = 4096
REASONING_EFFORT = "none"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF = 0.5
# Bump when the prompt or the yes/no contract changes so old cache rows miss.
PROMPT_VERSION = "2"
DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1/"

# Lockfiles and generated bundles are not useful edit targets. Callers can
# replace this list, or pass an empty sequence to disable it.
DEFAULT_SKIP_PATTERNS = (
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "npm-shrinkwrap.json",
    "bun.lock",
    "bun.lockb",
    "composer.lock",
    "Gemfile.lock",
    "Cargo.lock",
    "*.min.js",
    "*.min.mjs",
    "*.min.css",
)

_REASONING_MODEL = re.compile(
    r"(reasoner|reasoning|:thinking|-thinking|\bthinking\b|"
    r"(^|[/:])o[134]($|[/:_-])|gpt-5|deepseek-r1|\br1\b|qwq|magistral)",
    re.IGNORECASE,
)

_POLICIES = ("include", "exclude", "truncate")

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_THINKING_BLOCK = re.compile(r"<thinking\b[^>]*>.*?</thinking>", re.IGNORECASE | re.DOTALL)
_THINKING_OPEN = re.compile(r"<thinking\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_ANSWER_PREFIX = re.compile(
    r"^(?:answer|relevance|relevant|decision)\s*[:\-]\s*",
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
    in full (``binary``, ``oversize``, ``truncated``, or ``generated``).
    ``error`` is set when the call failed or the model returned an empty
    answer; the file is still included. ``failed_open`` marks that case.
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
        """Files included because the model returned an empty answer or the call failed."""
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

    Keys are the model, a hash of the task, a hash of the file contents, the
    truncation variant, the output-token limit, and the prompt version. A
    judgment made on a truncated body, or under a smaller completion budget,
    is not reused as a full-content judgment. Fallback and error decisions
    are not stored.
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
        variant: str = "full",
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        prompt_version: str = PROMPT_VERSION,
    ) -> str:
        task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return "\n".join([
            model,
            task_hash,
            content_hash,
            variant,
            str(max_output_tokens),
            str(prompt_version),
        ])

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
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
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


def parse_relevance_answer(raw: str) -> tuple[Optional[str], str]:
    """Parse a yes/no relevance answer.

    Returns ``(decision, cleaned)`` where ``decision`` is ``"yes"``, ``"no"``,
    or ``None`` when the answer is ambiguous. Think tags and chain-of-thought
    reasoning blocks are removed before the first line is read, so a "no"
    inside a think tag cannot override a final "yes".
    """
    from .core import swallow_reasoning

    text = strip_think_tags(raw or "")
    text, _reasoning = swallow_reasoning(text)
    text = text.strip()
    # Drop a wrapping code fence, if the whole answer is fenced.
    lines = text.splitlines()
    if len(lines) >= 2 and _FENCE_LINE.match(lines[0].strip()) and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    cleaned = text.strip().strip("`").strip()
    content_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not content_lines:
        return None, cleaned

    # A single consistent yes or no wins, including "Answer: yes" and a fenced
    # "NO". Both words, or neither, is ambiguous — callers include the file.
    answer_text = _ANSWER_PREFIX.sub("", content_lines[0]).strip()
    if len(content_lines) > 1:
        answer_text = answer_text + "\n" + "\n".join(content_lines[1:])
    words = re.findall(r"\b(yes|no)\b", answer_text, flags=re.IGNORECASE)
    lowered = [word.lower() for word in words]
    if lowered and len(set(lowered)) == 1:
        return lowered[0], cleaned
    return None, cleaned


def _check_policy(name: str, value: str) -> str:
    if value not in _POLICIES:
        raise ValueError(f"{name} must be one of {', '.join(_POLICIES)}; got {value!r}")
    return value


def _resolve_model(model: Optional[str]) -> str:
    from .env import get_env

    return model or get_env("GPTDIFF_FILTER_MODEL") or DEFAULT_FILTER_MODEL


def model_uses_reasoning(model: Optional[str]) -> bool:
    """True when the model name looks like it spends completion tokens on reasoning."""
    if not model:
        return False
    return _REASONING_MODEL.search(model) is not None


def output_token_budget(model: Optional[str], requested: int) -> int:
    """Raise the completion budget for reasoning models so a yes/no still fits."""
    requested = int(requested)
    if model_uses_reasoning(model):
        return max(requested, REASONING_MAX_OUTPUT_TOKENS)
    return requested


def reasoning_effort_for(model: Optional[str]) -> Optional[str]:
    """Ask reasoning models not to think. A yes/no check does not need it."""
    if model_uses_reasoning(model):
        return REASONING_EFFORT
    return None


def path_is_skipped(path: str, patterns) -> bool:
    """Match a skip glob against the basename and the full relative path."""
    normalized = str(path).replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(normalized, pattern):
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


def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def count_tokens(text: str, encoder=None) -> int:
    if not text:
        return 0
    encoder = _encoder() if encoder is None else encoder
    if encoder is None:
        return max(1, len(text) // 4)
    try:
        return len(encoder.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _truncate_text(text: str, max_bytes: Optional[int], max_tokens: Optional[int], encoder) -> str:
    out = text
    if max_tokens:
        if encoder is not None:
            try:
                ids = encoder.encode(out)
                if len(ids) > max_tokens:
                    out = encoder.decode(ids[:max_tokens])
            except Exception:
                if count_tokens(out, None) > max_tokens:
                    out = out[: max_tokens * 4]
        elif count_tokens(out, None) > max_tokens:
            out = out[: max_tokens * 4]
    if max_bytes:
        raw = out.encode("utf-8")
        if len(raw) > max_bytes:
            out = raw[:max_bytes].decode("utf-8", errors="ignore")
    return out


def is_binary_content(content) -> bool:
    """True when ``content`` looks like a binary file (NUL or non-text controls)."""
    if isinstance(content, (bytes, bytearray)):
        sample = bytes(content[:8192])
        if b"\x00" in sample:
            return True
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return _text_looks_binary(text)
    if not isinstance(content, str):
        return False
    return _text_looks_binary(content[:8192])


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
        return bytes(content).decode("utf-8", errors="replace")
    raise TypeError(f"file content must be str or bytes, got {type(content).__name__}")


def _usage_and_text(response):
    if isinstance(response, dict):
        content = response["choices"][0]["message"]["content"]
        usage = response.get("usage") or {}
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        total = usage.get("total_tokens") or (prompt + completion)
        return content or "", int(prompt), int(completion), int(total)

    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    prompt = completion = total = 0
    if usage:
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total = usage.get("total_tokens") or (prompt + completion)
        else:
            prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0) or 0
            completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0) or 0
            total = getattr(usage, "total_tokens", None) or (prompt + completion)
    return content, int(prompt), int(completion), int(total)


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
    return _usage_and_text(response)


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
    (disabled). The cache key is the model, a hash of the task, a hash of the
    file contents, the truncation variant, the output-token limit, and
    ``PROMPT_VERSION``. Clear yes/no answers are cached. Empty answers,
    ambiguous answers, and errors are not.

    Binary files and files over ``max_bytes`` / ``max_tokens`` are not sent in
    full. ``on_binary`` and ``on_oversize`` are ``include`` (keep, no model
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
    encoder = _encoder()
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
    # Group files that share a cache key so each distinct body is sent once.
    groups: dict[str, list] = {}
    group_order: list[str] = []

    for path, content in files.items():
        if path_is_skipped(path, patterns):
            by_path[path] = _skip_decision(
                path,
                "no",
                "generated",
                "skipped lockfile or generated file",
            )
            continue
        binary = is_binary_content(content)
        text = _coerce_text(content)
        if binary:
            if on_binary == "truncate":
                note = (
                    "Note: this file is binary. Its bytes are not shown; "
                    "judge relevance from the path and this note."
                )
                body = f"[binary file, {len(content) if isinstance(content, (bytes, bytearray)) else len(text.encode('utf-8'))} bytes, contents omitted]"
                variant = "binary-preview"
                item = _pending_item(path, text, body, note, "truncated", variant, task, model, output_budget)
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
        n_tokens = count_tokens(text, encoder)
        over_bytes = bool(max_bytes) and n_bytes > max_bytes
        over_tokens = bool(max_tokens) and n_tokens > max_tokens
        if over_bytes or over_tokens:
            if on_oversize == "truncate":
                body = _truncate_text(text, max_bytes, max_tokens, encoder)
                note = (
                    "Note: this file was truncated to fit the relevance check "
                    f"({n_bytes} bytes, {n_tokens} tokens). Judge from the portion shown."
                )
                variant = f"truncated:{max_tokens}:{max_bytes}"
                item = _pending_item(path, text, body, note, "truncated", variant, task, model, output_budget)
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

        item = _pending_item(path, text, text, None, None, "full", task, model, output_budget)
        _enqueue(groups, group_order, item)

    def _cached(key: str) -> bool:
        if cache_obj is None:
            return False
        hit = cache_obj.get(key)
        return bool(
            hit
            and hit.get("decision") in ("yes", "no")
            and not hit.get("error")
            and not hit.get("failed_open")
        )

    if any(not _cached(key) for key in group_order) and not api_key:
        raise RuntimeError(
            "GPTDIFF_LLM_API_KEY is required for the relevance filter. "
            "Set it in the environment or pass api_key=."
        )

    def run_group(key: str):
        items = groups[key]
        if cache_obj is not None:
            hit = cache_obj.get(key)
            if hit and hit.get("decision") in ("yes", "no") and not hit.get("error") and not hit.get("failed_open"):
                return [
                    _decision_from_record(item["path"], {**hit, "cache_key": key}, cached=True)
                    for item in items
                ]
        sample = items[0]

        def fail_open(message, raw=None, prompt_tokens=0, completion_tokens=0, total_tokens=0):
            # Errors and empty answers are not cached. The next call tries again.
            return [
                RelevanceDecision(
                    path=item["path"],
                    decision="yes",
                    reason=f"error; included by default: {message}",
                    raw=raw,
                    tokens=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached=index > 0 and total_tokens > 0,
                    skipped_reason=item["skipped_reason"],
                    error=message,
                    failed_open=True,
                    cache_key=key,
                )
                for index, item in enumerate(items)
            ]

        attempt = 0
        while True:
            try:
                raw, prompt_tokens, completion_tokens, total_tokens = _ask(
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
            decision = "yes"
            reason = f"ambiguous answer; included by default: {cleaned}"
            error = None
            store = False
        else:
            decision = parsed
            reason = cleaned
            error = None
            store = True
        record = {
            "decision": decision,
            "reason": reason,
            "raw": raw,
            "tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "skipped_reason": sample["skipped_reason"],
            "error": error,
            "failed_open": False,
            "cache_key": key,
        }
        if store and cache_obj is not None:
            cache_obj.put(key, record)
        decisions = []
        for index, item in enumerate(items):
            decisions.append(
                RelevanceDecision(
                    path=item["path"],
                    decision=decision,
                    reason=reason,
                    raw=raw,
                    tokens=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached=index > 0,
                    skipped_reason=item["skipped_reason"],
                    error=error,
                    failed_open=False,
                    cache_key=key,
                )
            )
        return decisions

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
            cache_obj.flush()

    decisions = [by_path[path] for path in files]
    included = {}
    for path, content in files.items():
        decision = by_path[path]
        if decision.decision == "yes":
            included[path] = content if isinstance(content, str) else _coerce_text(content)

    return FilterRelevantResult(files=included, decisions=decisions, model=model)


def _pending_item(path, original, body, note, skipped_reason, variant, task, model, max_output_tokens):
    key = RelevanceCache.make_key(
        model,
        task,
        original,
        variant,
        max_output_tokens=max_output_tokens,
        prompt_version=PROMPT_VERSION,
    )
    return {
        "path": path,
        "body": body,
        "note": note,
        "skipped_reason": skipped_reason,
        "key": key,
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
