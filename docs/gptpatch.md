---
title: gptpatch
---

# gptpatch Command-Line Tool

> **GPTDiff everywhere:** this library also ships for browser and Node as [gptdiff-js](https://github.com/255BITS/gptdiff-js) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

`gptpatch` is a companion tool to [GPTDiff](https://github.com/255BITS/gptdiff) that applies unified diffs directly to your project using GPTDiff’s patch logic.

## Overview

`gptpatch` accepts diffs either directly via a command-line argument or from a file. It first attempts to apply the diff using standard patch logic. If that fails, it falls back to a smart apply mechanism that leverages AI-powered conflict resolution.

## Usage

### Apply Diff from Command Line

```bash
gptpatch --diff "<diff text>"
```

### Apply Diff from a File

```bash
gptpatch path/to/diff.patch
```

## Options

- **--project-dir**: Specify the target directory for applying the diff (default: current directory)
- **--model**: (Optional) Specify the LLM model for advanced conflict resolution
- **--max_tokens**: (Optional) Maximum tokens to use for LLM responses
- **--nobeep**: Disable the completion beep notification
- **--dumb**: Attempt to apply the diff using standard patch logic (like git apply) before falling back to smart apply

## Workflow

By default, gptpatch uses an AI-powered smart apply mechanism to apply the diff directly. If the `--dumb` flag is specified, it first attempts to apply the diff using standard patch logic (similar to git apply). If that fails, it falls back to the smart apply mechanism with AI-powered conflict resolution.

## Agent Loop Integration

`gptpatch` is what makes GPTDiff agent loops production-ready. When running continuous improvement loops:

```bash
while true; do
  gptdiff "improve code quality" --apply
done
```

The `--apply` flag uses gptpatch internally. Each iteration may encounter:

- **Code drift** - Files changed since the diff was generated
- **Merge conflicts** - Overlapping changes from previous iterations
- **Partial matches** - Context lines that shifted

gptpatch's AI-powered smart apply resolves these automatically, keeping your loop running without manual intervention. This is why GPTDiff can run overnight while you sleep—gptpatch handles the edge cases.

For production-ready loop patterns and real metrics (18→127 tests, 8 SQL injections→0), see the [Agent Loops Guide](examples/automation.md).

For further details about GPTDiff and its companion tools, please refer to the [GPTDiff Documentation](https://gptdiff.255labs.xyz).