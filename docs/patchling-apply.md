---
title: patchling-apply
---

# patchling-apply Command-Line Tool

> **Patchling everywhere:** this library also ships for browser and Node as [patchling](https://github.com/255BITS/patchling) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

`patchling-apply` is a companion tool to [Patchling](https://github.com/255BITS/patchling-py) that applies unified diffs directly to your project using Patchling’s patch logic.

## Overview

`patchling-apply` accepts diffs either directly via a command-line argument or from a file. It first attempts to apply the diff using standard patch logic. If that fails, it falls back to a smart apply mechanism that leverages AI-powered conflict resolution.

## Usage

### Apply Diff from Command Line

```bash
patchling-apply --diff "<diff text>"
```

### Apply Diff from a File

```bash
patchling-apply path/to/diff.patch
```

## Options

- **--project-dir**: Specify the target directory for applying the diff (default: current directory)
- **--model**: (Optional) Specify the model used to apply the diff. This overrides `GPTDIFF_SMARTAPPLY_MODEL`; when neither is set, the default is `openai/gpt-4.1-mini`.
- **--max_tokens**: (Optional) Maximum tokens to use for LLM responses
- **--nobeep**: Disable the completion beep notification
- **--dumb**: Attempt to apply the diff using standard patch logic (like git apply) before falling back to smart apply

## Workflow

By default, patchling-apply uses an AI-powered smart apply mechanism to apply the diff directly. If the `--dumb` flag is specified, it first attempts to apply the diff using standard patch logic (similar to git apply). If that fails, it falls back to the smart apply mechanism with AI-powered conflict resolution.

## Exit status and recovery

The command exits with status `0` when every file succeeds. Deleting a file
that is already absent is a successful no-op. An empty or invalid diff, an
unsafe path, a model refusal or empty response, and a filesystem failure
produce status `1`.

Files are applied independently. If one file fails, successful writes or
deletions to other files may remain; they are not rolled back. The failure
summary lists each failed file and its reason. Inspect the project before
retrying, and make automation check the exit status before continuing.
The same failure status applies to `patchling "…" --apply` and the legacy
`gptdiff` / `gptpatch` command aliases. The in-memory library `smartapply`
retains its existing return and exception behavior.

## Agent Loop Integration

`patchling-apply` is what makes Patchling agent loops production-ready. When running continuous improvement loops:

```bash
while true; do
  patchling "improve code quality" --apply || exit 1
done
```

The `--apply` flag uses patchling-apply internally. Each iteration may encounter:

- **Code drift** - Files changed since the diff was generated
- **Merge conflicts** - Overlapping changes from previous iterations
- **Partial matches** - Context lines that shifted

Successful iterations can continue automatically. A nonzero exit status requires
the loop to stop or explicitly handle the reported failure before continuing.

For production-ready loop patterns and real metrics (18→127 tests, 8 SQL injections→0), see the [Agent Loops Guide](examples/automation.md).

For further details about Patchling and its companion tools, please refer to the [Patchling Documentation](https://255bits.github.io/patchling-py).
