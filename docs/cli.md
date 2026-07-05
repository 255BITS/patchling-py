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
**Choose LLM model (default: $GPTDIFF_MODEL or 'gemini-3-pro-preview')**
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
`--applymodel <model_name>`: Specify the model to use for applying the diff (used in smartapply). If not specified, defaults to the model from `--model` or `GPTDIFF_MODEL`.
`--nowarn`: Disable the warning and confirmation prompt for large token usage
`--verbose`: Enable verbose output for detailed information during execution

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
- `GPTDIFF_MODEL`: Default model for generating diffs (default: gemini-3-pro-preview)

For the smartapply feature, you can set separate variables:
- `GPTDIFF_SMARTAPPLY_MODEL`: Model for smartapply (recommended: `gpt5-mini`, fast and reliable for applying diffs; defaults to `GPTDIFF_MODEL` if not set)
- `GPTDIFF_SMARTAPPLY_API_KEY`: API key for smartapply (defaults to `GPTDIFF_LLM_API_KEY` if not set)
- `GPTDIFF_SMARTAPPLY_BASE_URL`: Base URL for smartapply (defaults to `GPTDIFF_LLM_BASE_URL` if not set)

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

