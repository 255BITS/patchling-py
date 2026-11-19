# Installation

> **Patchling everywhere:** this library also ships for browser and Node as [patchling](https://github.com/255BITS/patchling) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Prerequisites
- Python 3.10 or higher

## Install via pip

Install the latest published version from PyPI:

```bash
python -m pip install patchling
```

The [PyPI release history](https://pypi.org/project/patchling/#history) lists
which versions `pip` can install by version number.

## Install from source

Use an editable checkout to develop Patchling or try changes ahead of PyPI:

```bash
git clone https://github.com/255BITS/patchling-py.git
cd patchling-py
python -m pip install -e '.[test]'
```

Package metadata lives in `pyproject.toml`. The version is read from
`patchling.__version__`. `setup.py` supports older install commands.

## Configuration

### API Key and Base URL

To use `patchling`, you need an API key from [nano-gpt.com/api](https://nano-gpt.com/api). Once you get your API key, you need to set up the following environment variables.

#### Linux/MacOS
```bash
export GPTDIFF_LLM_API_KEY='your-api-key'
# Optional: override the default models
export GPTDIFF_MODEL='deepseek/deepseek-v4.1-flash-1017'  # For generating diffs (this is the default)
export GPTDIFF_SMARTAPPLY_MODEL='openai/gpt-5-mini'  # For applying diffs
```

#### Windows
```cmd
set GPTDIFF_LLM_API_KEY=your-api-key
rem Optional: For switching API providers
set GPTDIFF_LLM_BASE_URL=https://nano-gpt.com/api/v1/
```

The default base URL points to `nano-gpt.com`'s API. Supported models can be specified with:

```bash
patchling 'your prompt' --model $GPTDIFF_MODEL
```

---

## You're Ready

With Patchling installed, you can:

1. **Make one-off changes** - Describe what you want, get a diff, apply it
2. **Run agent loops** - Let Patchling work autonomously while you sleep

Most users start with single commands, then graduate to agent loops once they see the power:

```bash
# Start with a single change
patchling "Add input validation" --apply

# Then scale to autonomous improvement
while true; do patchling "Fix code quality issues" --apply; sleep 5; done
```

Real users have run overnight loops that expanded test coverage from 18 to 127 test cases, and security sprints that eliminated 8 SQL injection vulnerabilities and 12 XSS flaws automatically.

**Next steps:**
- [Quickstart Guide](quickstart.md) - Make your first change
- [Agent Loops Guide](examples/automation.md) - Set up autonomous improvement
