import pytest
from patchling.core import generate_diff

# Dummy classes to simulate an LLM response
class DummyMessage:
    def __init__(self, content):
        self.content = content

class DummyChoice:
    def __init__(self, content):
        self.message = DummyMessage(content)

class DummyUsage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

class DummyResponse:
    def __init__(self, content, prompt_tokens, completion_tokens, total_tokens):
        self.choices = [DummyChoice(content)]
        self.usage = DummyUsage(prompt_tokens, completion_tokens, total_tokens)

def test_fail_diff_through_call_llm(monkeypatch):
    diff_str = """```diff
DIFF 1
```

Some text here
```diff
DIFF 2
```"""

    expected = """
DIFF 1

DIFF 2"""

 
    # Define a dummy call_llm function that returns our fake response
    def dummy_call_llm(api_key, base_url, model, messages, max_tokens, budget_tokens, temperature):
        return DummyResponse(diff_str, prompt_tokens=10, completion_tokens=20, total_tokens=30)

    # Patch call_llm in the gptdiff module with our dummy function.
    monkeypatch.setattr("patchling.core.call_llm", dummy_call_llm)

    # generate_diff calls call_llm_for_diff internally, which now uses our dummy_call_llm.
    result = generate_diff("dummy environment", "dummy goal", model="test-model")

    assert result.strip() == expected.strip()
