# Testing Philosophy & Practices

> **Patchling everywhere:** this library also ships for browser and Node as [patchling](https://github.com/255BITS/patchling) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Core Testing Principles

### **Attributes of a good test**
- Zero cross-test contamination
- Pure functions with no side effects
- Deterministic results across runs

## Key Test Categories

### File Operations
```python
# tests/test_smartapply.py
def test_smartapply_file_deletion():
    """Verify clean removal without residual artifacts"""
    # Tests deletion idempotency and missing file safety

def test_smartapply_new_file_creation():
    """Validate file initialization from diffs"""
    # Ensures proper handling of /dev/null sources
```

### Complex Modifications
```python
def test_smartapply_complex_single_hunk(mocker):
    """Test multi-line changes with context preservation"""
    # Validates LLM mock integration and structural understanding

**Example Mock Setup:**
```python
# Mock LLM response for predictable testing
mocker.patch('patchling.patchling.call_llm_for_apply',
    return_value="def new():\n    print('Updated')")

# Verify transformation
updated = smartapply(diff, original_files)
assert "Updated" in updated["file.py"]
```


## Relevance filter

`tests/test_relevance.py` mocks the LLM. It covers the shared 1024-token / `reasoning_effort=low` request, the length-prefixed cache key (mode, note, and the effort actually sent, including a legacy SDK that rejects the parameter), the leading-token parser (a quoted decision followed by a reason), non-string message content failed open, malformed answers counted as failed open, a cache read treated as a miss, cache write errors, transient retries, invalid UTF-8 after the 8192-byte probe, a truncated UTF-8 tail that keeps its valid prefix, ignored binaries excluded before decode, a preserved leading BOM, and the canonical skip list (`ignored`, including `*.min.mjs`). `tests/fixtures/parity/relevance.json` is a byte-identical copy of the JS fixture and is checked against the library functions, including its sha256. `tests/test_relevance_live.py` is skipped unless `LIVE_LLM=1`, in which case it reads `GPTDIFF_LLM_API_KEY` and calls the configured endpoint. CI does not set `LIVE_LLM`, so it never makes that call. The benchmark in `bench/relevance/` uses the same gate. `tests/test_relevance_bench.py` checks that a single `generate_diff` failure is stored in the JSON report with the other pipeline's diff and applied files.

## Running the Test Suite

```bash
# Install development dependencies
pip install -e .[test]

# Run all tests with coverage
pytest tests/ --cov=patchling --cov-report=term-missing

# Target specific test cases
pytest tests/test_smartapply.py -k "test_smartapply_file_modification"
```

## Writing New Tests

1. **Isolate Scenarios**: One logical case per test
2. **Mock LLM Responses**: Use `@patch` for deterministic outcomes
3. **Check Boundaries**: Empty files, invalid paths, encoding issues

## Agent Loop Testing

Agent loops run autonomously, making test reliability critical. Flaky tests or inconsistent behavior will compound across hundreds of iterations.

**Testing for Loop Safety:**

```python
def test_operation_idempotency():
    """Verify repeated execution produces consistent results"""
    result1 = apply_operation(source_code)
    result2 = apply_operation(result1)  # Second pass
    assert result1 == result2  # Should be stable
```

**Why This Matters:** A test that passes sometimes but fails others becomes a major problem at 3 AM when your agent loop has run 47 iterations.

**Pro tip:** Use agent loops to improve your test suite. See [Test Enhancement Recipes](../examples/automation.md#test-enhancement-recipes) for patterns that can expand coverage while you sleep.
