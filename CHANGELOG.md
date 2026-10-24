# Changelog

## Unreleased

### Fixed

- **Default model is now `deepseek/deepseek-v4.1-flash-1017`.** `DEFAULT_MODEL` was `"deepseek-reasoner"`, which NanoGPT (the default `GPTDIFF_LLM_BASE_URL`) does not list. As a result, whenever `GPTDIFF_MODEL` was unset and no model was passed, `generate_diff` and the CLI's diff generation failed, and so did `smartapply` on its model-backed path: files whose patch could not be applied deterministically by `apply_patch_to_content`. Patches that applied deterministically, and file deletions, never called the model and were unaffected. The new default is the October 17, 2026 snapshot of DeepSeek V4.1 Flash as listed on NanoGPT ($0.10 / $0.60 per 1M input / output tokens, 1M context). NanoGPT lists no non-Flash V4.1 model. It matches the JS package's `DEFAULT_MODEL` (which replaces the retired, HTTP 410 `xiaomi/mimo-v2.5-pro-ultraspeed`). Callers that pass `model` or set `GPTDIFF_MODEL` are unaffected. The `filter_relevant` default (`xiaomi/mimo-v2.6-flash`) is unchanged. The bench runner's fallback diff model follows the new default, and the docs that state the default (README env table, `docs/api.md`, `docs/cli.md`) are updated.
