"""Opt-in per-file relevance filter.

Python counterpart of the JS library's ``filterRelevant``. For each candidate
file, a cheap OpenAI-compatible model is asked whether the file is relevant
to the task. Only ``yes`` files are returned for ``generate_diff`` and
``smartapply``.

Ambiguous answers and per-file errors default to include, and the error is
attached to that file's decision so callers can see it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

DEFAULT_FILTER_MODEL = "gemini-2.0-flash"
DEFAULT_CONCURRENCY = 8
DEFAULT_MAX_BYTES = 200_000
DEFAULT_MAX_TOKENS = 12_000
DEFAULT_MAX_OUTPUT_TOKENS = 256
DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1/"

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
    in full (``binary``, ``oversize``, or ``truncated``). ``error`` is set
    when the call failed; the file is still included.
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

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "files": list(self.files.keys()),
            "decisions": [d.to_dict() for d in self.decisions],
            "billed_tokens": self.billed_tokens,
            "billed_prompt_tokens": self.billed_prompt_tokens,
            "billed_completion_tokens": self.billed_completion_tokens,
            "errors": [d.to_dict() for d in self.errors],
        }


class RelevanceCache:
    """In-memory relevance cache with an optional on-disk JSON file.

    Keys are ``(model, task hash, file content hash)`` plus a variant tag so a
    judgment made on a truncated body is not reused as a full-content judgment.
    """

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = os.fspath(path) if path else None
        self._entries: dict = {}
        self._lock = threading.Lock()
        if self.path and os.path.exists(self.path):
            self._load()

    @staticmethod
    def make_key(model: str, task: str, content: str, variant: str = "full") -> str:
        task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"{model}\n{task_hash}\n{content_hash}\n{variant}"

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
    return model or os.getenv("GPTDIFF_FILTER_MODEL") or DEFAULT_FILTER_MODEL


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
):
    user_prompt = _build_user_prompt(task, paths, body, note)
    user_content = user_prompt
    if model and "gemini" in model:
        user_content = _SYSTEM_PROMPT + "\n" + user_prompt
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    response = call(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_output_tokens,
        temperature=0.0,
        budget_tokens=None,
    )
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
    file contents, and a truncation variant.

    Binary files and files over ``max_bytes`` / ``max_tokens`` are not sent in
    full. ``on_binary`` and ``on_oversize`` are ``include`` (keep, no model
    call), ``exclude`` (drop, no model call), or ``truncate`` (ask the model
    about a truncated or redacted preview). Pass ``None`` for ``max_bytes`` or
    ``max_tokens`` to disable that limit. Credentials resolve the same way as
    the rest of the library: explicit ``api_key`` / ``base_url``, then
    ``GPTDIFF_LLM_API_KEY`` and ``GPTDIFF_LLM_BASE_URL``.
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
    cache_obj = _resolve_cache(cache)
    encoder = _encoder()

    if api_key is None:
        api_key = os.getenv("GPTDIFF_LLM_API_KEY")
    if not base_url:
        base_url = os.getenv("GPTDIFF_LLM_BASE_URL", DEFAULT_BASE_URL)
    base_url = base_url or DEFAULT_BASE_URL

    if call_llm is None:
        from .core import call_llm as call_llm  # noqa: PLW0621

    by_path: dict[str, RelevanceDecision] = {}
    # Group files that share a cache key so each distinct body is sent once.
    groups: dict[str, list] = {}
    group_order: list[str] = []

    for path, content in files.items():
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
                item = _pending_item(path, text, body, note, "truncated", variant, task, model)
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
                item = _pending_item(path, text, body, note, "truncated", variant, task, model)
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

        item = _pending_item(path, text, text, None, None, "full", task, model)
        _enqueue(groups, group_order, item)

    def _cached(key: str) -> bool:
        if cache_obj is None:
            return False
        hit = cache_obj.get(key)
        return bool(hit and hit.get("decision") in ("yes", "no") and not hit.get("error"))

    if any(not _cached(key) for key in group_order) and not api_key:
        raise RuntimeError(
            "GPTDIFF_LLM_API_KEY is required for the relevance filter. "
            "Set it in the environment or pass api_key=."
        )

    def run_group(key: str):
        items = groups[key]
        if cache_obj is not None:
            hit = cache_obj.get(key)
            if hit and hit.get("decision") in ("yes", "no") and not hit.get("error"):
                return [
                    _decision_from_record(item["path"], {**hit, "cache_key": key}, cached=True)
                    for item in items
                ]
        sample = items[0]
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
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            return [
                RelevanceDecision(
                    path=item["path"],
                    decision="yes",
                    reason=f"error; included by default: {message}",
                    raw=None,
                    tokens=0,
                    cached=False,
                    skipped_reason=item["skipped_reason"],
                    error=message,
                    cache_key=key,
                )
                for item in items
            ]

        parsed, cleaned = parse_relevance_answer(raw)
        if parsed is None:
            decision = "yes"
            reason = cleaned or "ambiguous answer; included by default"
            if cleaned:
                reason = f"ambiguous answer; included by default: {cleaned}"
        else:
            decision = parsed
            reason = cleaned
        record = {
            "decision": decision,
            "reason": reason,
            "raw": raw,
            "tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "skipped_reason": sample["skipped_reason"],
            "error": None,
            "cache_key": key,
        }
        if cache_obj is not None:
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
                    error=None,
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


def _pending_item(path, original, body, note, skipped_reason, variant, task, model):
    key = RelevanceCache.make_key(model, task, original, variant)
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
    error_count = len(result.errors)
    line = (
        f"Relevance filter ({result.model}): kept {kept}/{total} files, "
        f"{result.billed_tokens} tokens"
    )
    if error_count:
        line += f", {error_count} error{'s' if error_count != 1 else ''}"
    lines = [line]
    for decision in result.decisions:
        if decision.error:
            lines.append(f"  error {decision.path}: {decision.error} (included by default)")
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
