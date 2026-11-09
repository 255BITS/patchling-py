# Changelog

## 0.9.5 — 2026-11-09

### Fixed

- CLI apply commands aggregate per-file failures and exit 1 on failed or partial
  application, unsafe paths, and empty/invalid diffs. Successful files from a
  partial application remain on disk; inspect the reported paths before retrying.
- `patchling-apply --model` now selects the application model, with CLI >
  `GPTDIFF_SMARTAPPLY_MODEL` > default precedence. The main `patchling --model`
  continues to select diff generation; `--applymodel` selects application.
- Concurrent relevance-cache writers merge only dirty records into the latest
  committed file under a shared Node/Python local-filesystem coordination
  protocol. Failed flushes retain pending records. Only provably dead local
  POSIX owners can be reclaimed; Windows never signals other cache owners.

### Compatibility

- Cache JSON and cache keys remain version 1; no data migration is required.
  A persistent `<cache-path>.lock/` sidecar directory is added. Do not remove
  active claims or the directory while writers are running.
- Coordination requires all writers to implement the protocol. Published npm
  `patchling 0.3.1` and Python `patchling <=0.9.4` ignore the claims and may
  overwrite entries. Use a separate cache file for upgraded Python, or disable
  disk caching, while any older writer remains active. Stop existing writers
  before copying their cache. Merging the JS fix does not upgrade installed
  npm clients. See `CACHE_PROTOCOL.md` for recovery and filesystem limits.
- CLI failure status is intentionally stricter. Public in-memory `smartapply`
  return/error semantics, dependencies, and Python >=3.10 support are unchanged.

## 0.9.4

### Fixed

- **Default model is now `deepseek/deepseek-v4.1-flash-1017`.** `DEFAULT_MODEL` was `"deepseek-reasoner"`, which NanoGPT (the default `GPTDIFF_LLM_BASE_URL`) does not list. As a result, `generate_diff`, `smartapply` and the CLI failed whenever `GPTDIFF_MODEL` was unset and no model was passed. The new default is the October 17, 2026 snapshot of DeepSeek V4.1 Flash as listed on NanoGPT ($0.10 / $0.60 per 1M input / output tokens, 1M context). NanoGPT lists no non-Flash V4.1 model. It matches the JS package's `DEFAULT_MODEL` (which replaces the retired, HTTP 410 `xiaomi/mimo-v2.5-pro-ultraspeed`). Callers that pass `model` or set `GPTDIFF_MODEL` are unaffected. The `filter_relevant` default (`xiaomi/mimo-v2.6-flash`) is unchanged. The bench runner's fallback diff model follows the new default, and the docs that state the default (README env table, `docs/api.md`, `docs/cli.md`) are updated.
