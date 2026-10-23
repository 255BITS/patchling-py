# Patchling CLI Reference

> **Patchling everywhere:** this library also ships for browser and Node as [patchling](https://github.com/255BITS/patchling) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Core Command Structure
```bash
patchling "<transformation-prompt>" [FILES...] [OPTIONS]
```

## Key Options

### .gitignore and .gptignore

Files matching .gitignore pattern or <b>.gptignore</b> patterns are ignored when no files are specified.

### Transformation Control
`--apply`  
**AI-powered patch application**  
*Example:*  
⚠️ Processes files concurrently for performance
```bash
patchling "Add null safety checks" --apply src/
```

`--apply` may sit before, between, or after the file arguments:

```bash
patchling "Add null safety checks" src/app.js --apply
patchling "Add null safety checks" src/a.js --apply src/b.js
```

`--call`  
**Generate diff without applying**  
*Example:*  
```bash
patchling "Modernize string formatting" --call
```

`--prepend <file>`
**Prepend custom instructions from file to system prompt**
*Example:*
```bash
patchling "Modernize string formatting" --prepend style-guide.txt
```
`--prepend <file_or_url>`: Prepend custom instructions from the specified file or URL to the system prompt

`--image <path>`
**Attach one or more images**
*Example:*
```bash
patchling "Explain the chart in the README and refactor accordingly" --image docs/chart.png --image docs/layout.png
```
Adds each image (base64-encoded) to the request so the LLM can use visual context when generating diffs.

`--temperature <0-2>`  
**Control transformation creativity**  
*Default:* 0.7  
*Example:*  
```bash
patchling "Refactor legacy API" --temperature 0.3
```

### Model Selection
`--model`
**Choose LLM model (default: $GPTDIFF_MODEL or `deepseek/deepseek-v4.1-flash-1017`, which is `DEFAULT_MODEL`)**
*Options:* `gemini-3-pro-preview` (recommended), `gpt-4o` (complex), `gemini-2.0-flash` (fast)
*Example:*
```bash
patchling "Translate docs to French" --model gemini-2.0-flash
```

### Scope Management
`--files`  
**Target specific paths**  
*Example:*  
```bash
patchling "Update config system" config/ utils/config_loader.py
```

`--max_tokens <number>`: Set the maximum number of tokens for the API response (default: 30000)
`--applymodel <model_name>`: Model used by `--apply` to rewrite each changed file (smartapply). If not given, the CLI uses `GPTDIFF_SMARTAPPLY_MODEL` when it is set and non-blank, and otherwise `openai/gpt-4.1-mini`. It does **not** fall back to `--model` or `GPTDIFF_MODEL`, which only choose the model that generates the diff. With `--apply`, every file in the diff is sent to this model, except deleted files (`+++ /dev/null`), which are removed without a model call. Each request uses `--max_tokens`.
`--nowarn`: Disable the warning and confirmation prompt for large token usage
`--verbose`: Enable verbose output for detailed information during execution

### Relevance filter

For a large tree, `--filter-relevant` asks a cheap model whether each file matters to the prompt and sends only the `yes` files on to diff generation. The same base URL and API key as the rest of the tool are used (`GPTDIFF_LLM_BASE_URL`, `GPTDIFF_LLM_API_KEY`). The model defaults to `GPTDIFF_FILTER_MODEL` or `xiaomi/mimo-v2.6-flash`. A pallets/flask pass reported recall 1.0 and about 0.8x the token overhead of the next model tried. Those figures are **UNVERIFIED**. Each check uses prompt version 3, 1024 output tokens, and `reasoning_effort=low`.

```bash
patchling "Add structured logging to the request handlers" --filter-relevant --call
patchling "Rename the helper" --filter-relevant --filter-model xiaomi/mimo-v2.6-flash --apply
```

| Flag | Meaning |
|------|---------|
| `--filter-relevant` | Turn the filter on |
| `--filter-model` | Model for the yes/no checks |
| `--filter-concurrency` | How many checks run at once (default 8) |
| `--filter-cache PATH` | JSON cache. The key hashes prompt version, model, output-token limit, the effort actually sent, task, path, mode, preview note, and the body sent |
| `--filter-max-bytes` / `--filter-max-tokens` | Threshold past which a file is not sent in full (defaults 200000 bytes and 12000 tokens; `0` disables) |
| `--filter-oversize` | `include`, `exclude`, or `truncate` (default `truncate`) |
| `--filter-binary` | `include`, `exclude`, or `truncate` (default `exclude`) |
| `--filter-skip-pattern GLOB` | Also skip this glob (repeatable). Defaults are case-insensitive and include lockfiles, `dist/`, `*.min.js`, `*.min.mjs`, and `*.pyc` |
| `--filter-no-skip-generated` | Send lockfiles and generated files to the relevance model. A skipped file's reason is `ignored` |
| `--filter-decisions PATH` | Write every per-file decision as JSON |

A leading `yes` or `no` is the decision. Anything else (including an empty answer, a negated sentence such as "There is no doubt…", or explanatory wording) keeps the file, marks it failed open, and is not cached. The summary counts every such file, one per path. Timeouts and 5xx responses are retried first. A cache read error is treated as a miss and the model is called; a cache write error does not stop the run. `--apply` patches the full original file map, including files the filter left out of the prompt. `--verbose` prints each decision. The library call is `filter_relevant` (JS: `filterRelevant`). The canonical skip list and the parity fixture are in the [API reference](api.md) and `tests/fixtures/parity/relevance.json`. A benchmark that scores a hand-labeled file list lives at `bench/relevance/`.

`--nobeep`  
**Silence completion alerts**  
*Example:*  
```bash
patchling "Remove deprecated features" --nobeep
```

### Environment Variables
Patchling uses the following environment variables:
- `GPTDIFF_LLM_API_KEY`: API key for the LLM service
- `GPTDIFF_LLM_BASE_URL`: Base URL for the LLM API (default: https://nano-gpt.com/api/v1/)
- `GPTDIFF_MODEL`: Default model for generating diffs (default: `deepseek/deepseek-v4.1-flash-1017`, exported as `DEFAULT_MODEL`)
- `GPTDIFF_FILTER_MODEL`: Cheap model for `--filter-relevant` (default: xiaomi/mimo-v2.6-flash). Flask recall 1.0 and ~0.8x overhead figures for that default are UNVERIFIED.

For the CLI's `--apply` step (smartapply), you can set separate variables:
- `GPTDIFF_SMARTAPPLY_MODEL`: Model for applying diffs. `--applymodel` overrides it. If it is unset or blank, the CLI uses `openai/gpt-4.1-mini`, not `GPTDIFF_MODEL`.
- `GPTDIFF_SMARTAPPLY_API_KEY`: API key for smartapply (defaults to `GPTDIFF_LLM_API_KEY` if not set)
- `GPTDIFF_SMARTAPPLY_BASE_URL`: Base URL for smartapply (defaults to `GPTDIFF_LLM_BASE_URL`, then `https://nano-gpt.com/api/v1/`, if not set)

These variables are read by the CLI only. The Python `smartapply()` function uses its `model` argument, or else `GPTDIFF_MODEL`, then `DEFAULT_MODEL`. It also tries a deterministic patch first and calls the model only for files that patch cannot handle.

These allow you to use different models or credentials for generating and applying diffs—perfect for virtual team flexibility!

## Agent Loops

The CLI's `--apply` flag enables **continuous improvement automation**. Wrap any command in a loop for hands-free code enhancement:

```bash
while true; do
  patchling "Fix bugs and improve code quality" --apply
  sleep 5
done
```

This pattern unlocks Patchling's most powerful capability—autonomous code improvement that compounds over time. Each iteration finds and fixes issues you'd otherwise spend hours hunting down.

**Real impact:** One overnight loop took test coverage from 18 to 127 cases—what would take 2-3 days of manual work completed while you slept.

**Popular loop recipes:**
- Test coverage expansion
- Security vulnerability scanning
- Tech debt reduction
- Documentation sync

See [Agent Loops](examples/automation.md) for battle-tested patterns and advanced configurations.

