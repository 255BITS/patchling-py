# Basic Examples

> **GPTDiff everywhere:** this library also ships for browser and Node as [gptdiff-js](https://github.com/255BITS/gptdiff-js) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Single-File Refactor

```bash
# Before: utils.py
def process_data(input):
    result = []
    for item in input:
        result.append(item*2)
    return result

gptdiff "Convert loop to list comprehension" utils.py --apply
```

```python
# After: utils.py
def process_data(input):
    return [item*2 for item in input]
```

## Multi-File Rename

```bash
gptdiff "Rename UserController to AccountController" \
    app/controllers/user_controller.py \
    test/controllers/test_user_controller.py \
    docs/api.md --apply
```

## Full-Stack Type Safety

```bash
gptdiff "Add Python type hints throughout codebase" \
    --model deepseek-reasoner \
    --temperature 0.3 \
    --apply
```

## Legacy Modernization

```bash
gptdiff "Convert string formatting to f-strings" src/ --apply
```

---

## Ready for More?

These examples show what GPTDiff can do with a single command. But the real power comes from **running it continuously**.

```bash
while true; do
  gptdiff "Improve code quality" --apply
  sleep 5
done
```

Let GPTDiff work on your codebase while you sleep. Each cycle finds the next improvement, applies it, and continues—test coverage expands, tech debt shrinks, security hardens automatically.

**[See Agent Loops Guide →](automation.md)** for ready-to-use automation recipes.
