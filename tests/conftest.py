import pytest


class FakeEncoding:
    """Stand-in for a tiktoken Encoding. One token per character."""

    def encode(self, text, *args, **kwargs):
        return [0] * len(text or "")


@pytest.fixture
def fake_tiktoken(monkeypatch):
    """Stub tiktoken.get_encoding so a test needs no network or warm cache.

    On a cold cache, tiktoken.get_encoding("o200k_base") downloads the BPE file
    from openaipublic.blob.core.windows.net. patchling.core calls it in
    call_llm_for_diff and main(). Token counts are only used for logging and
    the large-prompt warning, so the exact counts do not matter in tests.
    """
    monkeypatch.setattr("patchling.core.tiktoken.get_encoding", lambda name: FakeEncoding())
    return FakeEncoding
