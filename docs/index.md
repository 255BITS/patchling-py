# GPTDiff Documentation

**Natural-language code transformation, as a library.** GPTDiff turns a plain-English goal plus a dict of files into a unified diff, then applies it resiliently with `smartapply`. It's a bounded primitive for embedding in your own software systems — pipelines, backends, products — not an open-ended coding agent.

```python
from gptdiff import generate_diff, smartapply, build_environment

files = {"main.py": "def old_name():\n    print('Need renaming')\n"}

diff = generate_diff(build_environment(files), "Rename old_name to new_name")
updated = smartapply(diff, files)
```

Files in, files out — no filesystem required. The diff is plain text you can log, review, or gate before applying, so your system stays in control.

---

## The GPTDiff family

| Project | What it is |
|---------|------------|
| **gptdiff** (these docs) | Python library + CLI tools — [PyPI](https://pypi.org/project/gptdiff/) · [GitHub](https://github.com/255BITS/gptdiff) |
| **[gptdiff-js](https://github.com/255BITS/gptdiff-js)** | Zero-dependency ESM port for browser and Node — same `generateDiff` + `smartapply` primitive |
| **[nanoodle.com](https://nanoodle.com)** | Visual AI workflow editor built on gptdiff-js — no server, no signup, bring your own key. The primitive, live in production |
| **[Live demos](https://255bits.github.io/gptdiff-js-examples/)** | Browser examples: LLM-edited games, 3D scenes, overlays, AI characters |

---

## How it works

1. **Build an environment** — serialize your files dict with `build_environment` (or scan a project directory with `load_project_files`)
2. **Generate a diff** — `generate_diff` sends context + your goal to an LLM and returns a unified diff
3. **Apply it** — `smartapply` patches per-file with AI-assisted conflict resolution, surviving hunks that `git apply` rejects

It works with any programming language and any OpenAI-compatible LLM endpoint.

---

## Quick Start

### 1. Install

```bash
pip install gptdiff
```

### 2. Configure

Get an API key from [nano-gpt.com/api](https://nano-gpt.com/api) (or use your own OpenAI-compatible endpoint via `GPTDIFF_LLM_BASE_URL`), then:

```bash
# Linux/macOS
export GPTDIFF_LLM_API_KEY='your-api-key'

# Windows
set GPTDIFF_LLM_API_KEY=your-api-key
```

### 3. Use

In your code — see the [API Reference](api.md) — or on a project directory via the CLI:

```bash
cd your-project
gptdiff "Add type hints to all functions" --apply
```

For detailed setup instructions, see the [Installation Guide](installation.md).

---

## The CLI: git-native workflow

The `gptdiff` command scans a project (respecting `.gitignore`), generates a diff, and optionally applies it:

| Command | What It Does | Use Case |
|---------|--------------|----------|
| `gptdiff "prompt"` | Generates prompt.txt only | Preview what will be sent to the AI |
| `gptdiff "prompt" --call` | Generates diff.patch | Review changes before applying |
| `gptdiff "prompt" --apply` | Generates and applies diff | Ready to make changes |

```bash
gptdiff "Refactor authentication to use JWT" --apply
git diff          # review
git add -p        # keep what you want
git checkout .    # discard the rest
```

---

## Agent Loops: bounded steps, composed

Each invocation is one goal → one diff, which makes GPTDiff safe to run in loops:

```bash
while true; do
  gptdiff "Add missing test cases for edge conditions" --apply
  sleep 5
done
```

**Real results** from one overnight run on a Python project:

| Metric | Before | After |
|--------|--------|-------|
| Test cases | 18 | 127 |
| Functions with tests | 12% | 71% |

For detailed patterns and recipes, see the [Automation Guide](examples/automation.md).

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Python API](api.md) | `generate_diff`, `smartapply`, and friends — start here for library use |
| [Quickstart](quickstart.md) | Get running in 2 minutes |
| [CLI Reference](cli.md) | All command-line options |
| [gptpatch](gptpatch.md) | Apply existing diffs with smartapply fallback |
| [Agent Loops](examples/automation.md) | Autonomous improvement recipes |
| [Core Concepts](concepts.md) | How GPTDiff works under the hood |
| [Installation](installation.md) | Setup and configuration |
| [Troubleshooting](troubleshooting.md) | Common issues and solutions |

**Model Selection:** see [Choosing a Model](https://github.com/255BITS/gptdiff#choosing-a-model) in the README.

---

## Links

- [GitHub Repository](https://github.com/255BITS/gptdiff) — source code (MIT licensed)
- [PyPI Package](https://pypi.org/project/gptdiff/) — install with pip
- [gptdiff-js](https://github.com/255BITS/gptdiff-js) — browser/Node port
- [nanoodle.com](https://nanoodle.com) — visual AI workflow editor built on gptdiff-js
- Built with [AI Agent Toolbox](https://toolbox.255labs.xyz)
