# GPTDiff

**Natural-language code transformation, as a library.** Hand GPTDiff a dict of files and a plain-English goal; get back a unified diff — and, via `smartapply`, the transformed files. It's a bounded primitive you embed inside your own software systems, not an open-ended coding agent.

```python
from gptdiff import generate_diff, smartapply, build_environment

files = {"main.py": "def old_name():\n    print('Need renaming')\n"}

diff = generate_diff(build_environment(files), "Rename old_name to new_name")
updated = smartapply(diff, files)

print(updated["main.py"])
```

Files in, files out. No filesystem access required, no agent harness. The hard part — applying an LLM-generated diff that `git apply` would reject — is what `smartapply` solves: per-file, AI-assisted patch resolution that survives fuzzy hunks, renames, new files, and deletions.

📚 Full documentation at [gptdiff.255labs.xyz](https://gptdiff.255labs.xyz)

Prefer the browser? **[gptdiff-js](https://github.com/255BITS/gptdiff-js)** is a JavaScript port of
`generateDiff` + `smartapply` — try the **[live demos →](https://255bits.github.io/gptdiff-js-examples/)**.

> **The gptdiff family** —
> **gptdiff** (you are here) ·
> [**gptdiff-js**](https://github.com/255BITS/gptdiff-js) (browser-first JS port) ·
> [**gptdiff-js-examples**](https://github.com/255BITS/gptdiff-js-examples) (live browser demos)

---

## The GPTDiff family

The same primitive exists for every runtime, and it powers a real product:

| Project | What it is |
|---------|------------|
| **gptdiff** (this repo) | Python library + CLI tools — [PyPI](https://pypi.org/project/gptdiff/) |
| **[gptdiff-js](https://github.com/255BITS/gptdiff-js)** | Zero-dependency ESM port for browser and Node — `generateDiff` + `smartapply` on in-memory file maps |
| **[nanoodle.com](https://nanoodle.com)** | Visual AI workflow editor built on gptdiff-js — no server, no signup, bring your own key. See the primitive working in production |
| **[Live demos](https://255bits.github.io/gptdiff-js-examples/)** | Browser examples: LLM-edited games, 3D scenes, stream overlays, AI characters |

Building for the browser? Start with [gptdiff-js](https://github.com/255BITS/gptdiff-js). Building a Python backend, pipeline, or your own agent? You're in the right repo.

---

## Quick Start

### 1. Install

```bash
pip install gptdiff
```

### 2. Set your API key

Works with any OpenAI-compatible endpoint. Get a key at [nano-gpt.com/api](https://nano-gpt.com/api), or point `GPTDIFF_LLM_BASE_URL` at your own provider.

```bash
# Linux/macOS
export GPTDIFF_LLM_API_KEY='your-api-key'

# Windows
set GPTDIFF_LLM_API_KEY=your-api-key
```

### 3. Transform files in your code

```python
from gptdiff import generate_diff, smartapply, build_environment

files = {
    "models.py": "class User:\n    name = CharField()",
    "tests/test_models.py": "def test_user():\n    User(name='Test').save()",
}

diff = generate_diff(
    build_environment(files),
    "Rename the 'name' field to 'username' across all layers",
)
files = smartapply(diff, files)
```

The diff is plain unified-diff text — log it, review it, gate it behind approval, or apply it immediately. That's the point: your system stays in control of what changes and when.

See [examples/usage_example.py](examples/usage_example.py) for a runnable version.

---

## Core API

- `generate_diff(environment: str, goal: str, model: str = ...) -> str` — generates a unified diff implementing the goal. `model` defaults to the `GPTDIFF_MODEL` env var.
- `smartapply(diff_text: str, files: dict[str, str], model: str = ...) -> dict[str, str]` — applies a diff with AI-powered conflict resolution. Handles new files, deletions, and hunks that standard patching rejects. Returns a new dict; input is not mutated.
- `build_environment(files: dict[str, str]) -> str` — serializes a files dict into the environment string `generate_diff` expects.
- `load_project_files(path, cwd) -> dict` / `save_files(files, base_dir)` — optional filesystem helpers for when you *do* want to read/write a real project (respects `.gitignore` and `.gptignore`).

Full signatures, error handling, and edge cases: [API Reference](https://gptdiff.255labs.xyz/api).

**Pipeline example** — sequential transformations over an in-memory codebase:

```python
from gptdiff import generate_diff, smartapply, build_environment

files = load_your_codebase()  # dict of {path: content}

for task in [
    "Add python type annotations",
    "Convert string formatting to f-strings",
    "Update deprecated API calls",
]:
    files = smartapply(generate_diff(build_environment(files), task), files)
```

This is the pattern [nanoodle.com](https://nanoodle.com) runs in the browser (via [gptdiff-js](https://github.com/255BITS/gptdiff-js)): each workflow node is a bounded diff→apply step over an in-memory file map, and the app never touches a server.

---

## Choosing a Model

**Reasoning models** produce more accurate diffs for complex changes; **fast models** win for applying diffs and simple edits.

| Model | Best for | Notes |
|-------|----------|-------|
| `gemini-3-pro-preview` | Generating diffs | **Recommended default** |
| `gpt-4o` / `claude-sonnet-4-20250514` | Complex or context-sensitive changes | Slower, more careful |
| `gpt5-mini` | Applying diffs (`smartapply`) | Fast and reliable — best `GPTDIFF_SMARTAPPLY_MODEL` |
| `gemini-2.0-flash` | Simple text changes | Most cost-effective |

```bash
export GPTDIFF_MODEL='gemini-3-pro-preview'
export GPTDIFF_SMARTAPPLY_MODEL='gpt5-mini'
```

### Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `GPTDIFF_LLM_API_KEY` | API key (required) | — |
| `GPTDIFF_MODEL` | Model for diff generation | `gemini-3-pro-preview` |
| `GPTDIFF_SMARTAPPLY_MODEL` | Model for applying diffs | `GPTDIFF_MODEL` |
| `GPTDIFF_LLM_BASE_URL` | OpenAI-compatible endpoint | `https://nano-gpt.com/api/v1/` |

---

## Command-Line Tools

The library also ships two CLIs for working on a real project directory.

### gptdiff

Describe a change; GPTDiff scans the project (respecting `.gitignore`/`.gptignore`), generates a diff, and optionally applies it:

| Command | What it does |
|---------|--------------|
| `gptdiff "prompt"` | Writes `prompt.txt` only — preview what would be sent |
| `gptdiff "prompt" --call` | Generates the diff into `diff.patch` for review |
| `gptdiff "prompt" --apply` | Generates and applies in one step |

```bash
cd your-project
gptdiff "Add type hints to all functions" --apply

# Target specific paths
gptdiff "Add logging" src/api/ src/utils/helpers.py
```

Useful flags: `--model`, `--temperature`, `--prepend <file>` (custom instructions), `--image <path>` (visual context), `--nobeep`. Full list: [CLI Reference](https://gptdiff.255labs.xyz/cli).

Because changes arrive as diffs, the CLI is git-native: review with `git diff`, keep with `git add -p`, discard with `git checkout .`.

### gptpatch

Applies an existing unified diff to a project — standard patch logic first, `smartapply` fallback when that fails:

```bash
gptpatch path/to/diff.patch
gptpatch --diff "<diff text>"
```

Options: `--project-dir`, `--model`, `--max_tokens`, `--nobeep`. Details: [gptpatch docs](https://gptdiff.255labs.xyz/gptpatch).

### Agent loops

Because each invocation is bounded (one goal → one diff), the CLI composes into loops:

```bash
while true; do
  gptdiff "Add missing test cases for edge conditions" --apply
  git add -A && git commit -m "Auto-improvement $(date +%H:%M)" 2>/dev/null
  sleep 30
done
```

One overnight test-coverage loop took a project from 18 to 127 test cases. Recipes and guardrails: [Automation Guide](https://gptdiff.255labs.xyz/examples/automation).

---

## Testing

```bash
pip install -e .[test]
pytest tests/
```

## Documentation

Docs live at [gptdiff.255labs.xyz](https://gptdiff.255labs.xyz). To preview locally:

```bash
pip install .[docs]
mkdocs serve
```

## Related projects

- [gptdiff-js](https://github.com/255BITS/gptdiff-js) — the browser/Node port
- [nanoodle.com](https://nanoodle.com) — visual AI workflow editor built on gptdiff-js
- [gptdiff-js live demos](https://255bits.github.io/gptdiff-js-examples/)
- [AI Agent Toolbox](https://github.com/255BITS/ai-agent-toolbox) — powers GPTDiff's tool-call parsing across models

MIT licensed. Built by [255labs](https://255labs.xyz).
