# Installation

> **GPTDiff everywhere:** this library also ships for browser and Node as [gptdiff-js](https://github.com/255BITS/gptdiff-js) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Prerequisites
- Python 3.8 or higher

## Install via pip

To install `gptdiff` from PyPI, run the following command:

```bash
pip install gptdiff
pip install tiktoken  # For token counting
```

## Development Installation (if no pip package is available yet)

If the `gptdiff` package is not yet available on PyPI, you can install it from the source code:

```bash
git clone https://github.com/255BITS/gptdiff.git
cd gptdiff
python setup.py install
```

## Configuration

### API Key and Base URL

To use `gptdiff`, you need an API key from [nano-gpt.com/api](https://nano-gpt.com/api). Once you get your API key, you need to set up the following environment variables.

#### Linux/MacOS
```bash
export GPTDIFF_LLM_API_KEY='your-api-key'
# Recommended models
export GPTDIFF_MODEL='gemini-3-pro-preview'  # For generating diffs
export GPTDIFF_SMARTAPPLY_MODEL='gpt5-mini'  # For applying diffs
```

#### Windows
```cmd
set GPTDIFF_LLM_API_KEY=your-api-key
rem Optional: For switching API providers
set GPTDIFF_LLM_BASE_URL=https://nano-gpt.com/api/v1/
```

The default base URL points to `nano-gpt.com`'s API. Supported models can be specified with:

```bash
gptdiff 'your prompt' --model $GPTDIFF_MODEL
```

---

## You're Ready

With GPTDiff installed, you can:

1. **Make one-off changes** - Describe what you want, get a diff, apply it
2. **Run agent loops** - Let GPTDiff work autonomously while you sleep

Most users start with single commands, then graduate to agent loops once they see the power:

```bash
# Start with a single change
gptdiff "Add input validation" --apply

# Then scale to autonomous improvement
while true; do gptdiff "Fix code quality issues" --apply; sleep 5; done
```

Real users have run overnight loops that expanded test coverage from 18 to 127 test cases, and security sprints that eliminated 8 SQL injection vulnerabilities and 12 XSS flaws automatically.

**Next steps:**
- [Quickstart Guide](quickstart.md) - Make your first change
- [Agent Loops Guide](examples/automation.md) - Set up autonomous improvement