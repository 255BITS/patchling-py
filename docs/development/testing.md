# Testing Philosophy & Practices

> **GPTDiff everywhere:** this library also ships for browser and Node as [gptdiff-js](https://github.com/255BITS/gptdiff-js) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

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
mocker.patch('gptdiff.gptdiff.call_llm_for_apply',
    return_value="def new():\n    print('Updated')")

# Verify transformation
updated = smartapply(diff, original_files)
assert "Updated" in updated["file.py"]
```


## Running the Test Suite

```bash
# Install development dependencies
pip install -e .[test]

# Run all tests with coverage
pytest tests/ --cov=gptdiff --cov-report=term-missing

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
