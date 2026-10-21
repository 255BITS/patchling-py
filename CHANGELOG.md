# Changelog

## Unreleased

### Fixed

- **Default model is now `xiaomi/mimo-v2.6-pro`.** `DEFAULT_MODEL` was `"deepseek-reasoner"`, which NanoGPT (the default `GPTDIFF_LLM_BASE_URL`) does not list. As a result, `generate_diff`, `smartapply` and the CLI failed whenever `GPTDIFF_MODEL` was unset and no model was passed. The new default is the current MiMo Pro listed on NanoGPT ($0.435 / $0.87 per 1M input / output tokens) and matches the JS package's `DEFAULT_MODEL` (which replaces the retired, HTTP 410 `xiaomi/mimo-v2.5-pro-ultraspeed`). Callers that pass `model` or set `GPTDIFF_MODEL` are unaffected. The `filter_relevant` default (`xiaomi/mimo-v2.6-flash`) is unchanged. The bench runner's fallback diff model follows the new default, and the docs that state the default (README env table, `docs/api.md`, `docs/cli.md`) are updated.
