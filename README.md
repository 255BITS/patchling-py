# Patchling

> _Formerly **gptdiff**. Same library, same API — `pip install patchling` (the `gptdiff` package still resolves during the transition)._

**Natural-language code transformation, as a library.** Hand Patchling a dict of files and a plain-English goal; get back a unified diff — and, via `smartapply`, the transformed files. It's a bounded primitive you embed inside your own software systems, not an open-ended coding agent.

```python
from patchling import generate_diff, smartapply, build_environment

files = {"main.py": "def old_name():\n    print('Need renaming')\n"}

diff = generate_diff(build_environment(files), "Rename old_name to new_name")
updated = smartapply(diff, files)

print(updated["main.py"])
```

Files in, files out. No filesystem access required, no agent harness. The hard part — applying an LLM-generated diff that `git apply` would reject — is what `smartapply` solves: per-file, AI-assisted patch resolution that survives fuzzy hunks, renames, new files, and deletions.

🌐 Project home: [patchling.app](https://patchling.app) — try it live in your browser · 📚 Full documentation at [255bits.github.io/patchling-py](https://255bits.github.io/patchling-py)

Prefer the browser? The same primitive ships on npm as **[patchling for JS](https://github.com/255BITS/patchling)**
(`generateDiff` + `smartapply`, zero-dependency ESM) — try the **[live demos →](https://255bits.github.io/patchling-examples/)**.

---

## The Patchling family

The same primitive exists for every runtime, and it powers a real product:

| Project | What it is |
|---------|------------|
| **patchling for Python** (this repo) | Python library + CLI tools — [PyPI](https://pypi.org/project/patchling/) |
| **[patchling for JS](https://github.com/255BITS/patchling)** | Zero-dependency ESM package for browser and Node — `generateDiff` + `smartapply` on in-memory file maps — [npm](https://www.npmjs.com/package/patchling) |
| **[patchling.app](https://patchling.app)** | Project homepage with a live in-browser demo of the whole diff → smartapply loop |
| **[nanoodle.com](https://nanoodle.com)** | Visual AI workflow editor built on patchling — no server, no signup, bring your own key. See the primitive working in production |
| **[Live demos](https://255bits.github.io/patchling-examples/)** | Browser examples: LLM-edited games, 3D scenes, stream overlays, AI characters |

Building for the browser? Start with [patchling for JS](https://github.com/255BITS/patchling). Building a Python backend, pipeline, or your own agent? You're in the right repo.

---

## Quick Start

### 1. Install

```bash
pip install patchling
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
from patchling import generate_diff, smartapply, build_environment

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

Full signatures, error handling, and edge cases: [API Reference](https://255bits.github.io/patchling-py/api).

**Pipeline example** — sequential transformations over an in-memory codebase:

```python
from patchling import generate_diff, smartapply, build_environment

files = load_your_codebase()  # dict of {path: content}

for task in [
    "Add python type annotations",
    "Convert string formatting to f-strings",
    "Update deprecated API calls",
]:
    files = smartapply(generate_diff(build_environment(files), task), files)
```

This is the pattern [nanoodle.com](https://nanoodle.com) runs in the browser (via [patchling](https://github.com/255BITS/patchling)): each workflow node is a bounded diff→apply step over an in-memory file map, and the app never touches a server.

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

> The former command names **`gptdiff`** and **`gptpatch`** still work as aliases for `patchling` and `patchling-apply`, so existing scripts don't break.

### patchling

Describe a change; Patchling scans the project (respecting `.gitignore`/`.gptignore`), generates a diff, and optionally applies it:

| Command | What it does |
|---------|--------------|
| `patchling "prompt"` | Writes `prompt.txt` only — preview what would be sent |
| `patchling "prompt" --call` | Generates the diff into `diff.patch` for review |
| `patchling "prompt" --apply` | Generates and applies in one step |

```bash
cd your-project
patchling "Add type hints to all functions" --apply

# Target specific paths
patchling "Add logging" src/api/ src/utils/helpers.py
```

Useful flags: `--model`, `--temperature`, `--prepend <file>` (custom instructions), `--image <path>` (visual context), `--nobeep`. Full list: [CLI Reference](https://255bits.github.io/patchling-py/cli).

Because changes arrive as diffs, the CLI is git-native: review with `git diff`, keep with `git add -p`, discard with `git checkout .`.

### patchling-apply

Applies an existing unified diff to a project — standard patch logic first, `smartapply` fallback when that fails:

```bash
patchling-apply path/to/diff.patch
patchling-apply --diff "<diff text>"
```

Options: `--project-dir`, `--model`, `--max_tokens`, `--nobeep`. Details: [patchling-apply docs](https://255bits.github.io/patchling-py/patchling-apply).

### Agent loops

Because each invocation is bounded (one goal → one diff), the CLI composes into loops:

```bash
while true; do
  patchling "Add missing test cases for edge conditions" --apply
  git add -A && git commit -m "Auto-improvement $(date +%H:%M)" 2>/dev/null
  sleep 30
done
```

One overnight test-coverage loop took a project from 18 to 127 test cases. Recipes and guardrails: [Automation Guide](https://255bits.github.io/patchling-py/examples/automation).

---

## Testing

```bash
pip install -e .[test]
pytest tests/
```

## Documentation

Docs live at [255bits.github.io/patchling-py](https://255bits.github.io/patchling-py). To preview locally:

```bash
pip install .[docs]
mkdocs serve
```

## Related projects

- [patchling.app](https://patchling.app) — project home, with a live in-browser demo
- [patchling for JS](https://github.com/255BITS/patchling) — the browser/Node package on [npm](https://www.npmjs.com/package/patchling)
- [nanoodle.com](https://nanoodle.com) — visual AI workflow editor built on patchling
- [patchling live demos](https://255bits.github.io/patchling-examples/)
- [AI Agent Toolbox](https://github.com/255BITS/ai-agent-toolbox) — powers Patchling's tool-call parsing across models

MIT licensed. Built by [255labs](https://255labs.xyz).
