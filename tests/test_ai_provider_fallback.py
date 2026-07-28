"""Tests for provider truncation detection and fallback engagement.

The bug these lock down: a token-truncated response is *non-empty*, so it used
to be returned as if it were a complete answer. The Groq fallback existed but
never fired, because nothing raised. Callers then failed to parse it and told
the student "the AI returned an invalid schedule".

All mocked — these must not consume API quota.
"""

import pytest

import ai_provider
from ai_provider import AITruncatedError


class _FR:
    def __init__(self, name): self.name = name


class _Resp:
    """Minimal stand-in for a google-genai response."""
    def __init__(self, text, finish="STOP"):
        self.text = text
        self.usage_metadata = type("U", (), {
            "thoughts_token_count": 7541, "candidates_token_count": 444})()
        self.candidates = [type("C", (), {"finish_reason": _FR(finish), "content": None})()]


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.setattr(ai_provider, "gemini_api_key", lambda: None)
    monkeypatch.setattr(ai_provider, "groq_api_key", lambda: None)


@pytest.fixture
def gemini_only(monkeypatch):
    monkeypatch.setattr(ai_provider, "gemini_api_key", lambda: "g-key")
    monkeypatch.setattr(ai_provider, "groq_api_key", lambda: None)


@pytest.fixture
def both_keys(monkeypatch):
    monkeypatch.setattr(ai_provider, "gemini_api_key", lambda: "g-key")
    monkeypatch.setattr(ai_provider, "groq_api_key", lambda: "q-key")


MSGS = [{"role": "user", "content": "hi"}]


# ── truncation detection ──────────────────────────────────────────


def test_finish_reason_max_tokens_is_detected():
    assert ai_provider._finish_reason(_Resp("partial", "MAX_TOKENS")) == "MAX_TOKENS"


def test_truncated_gemini_response_raises_instead_of_returning(gemini_only, monkeypatch):
    monkeypatch.setattr(ai_provider, "_gemini_client",
                        lambda: type("C", (), {"models": type("M", (), {
                            "generate_content": staticmethod(
                                lambda **kw: _Resp('{"schedule": [{"a', "MAX_TOKENS"))})()})())
    with pytest.raises(RuntimeError) as e:
        ai_provider.chat(MSGS, max_tokens=8000)
    assert "8000" in str(e.value)


def test_truncation_makes_the_fallback_engage(both_keys, monkeypatch):
    """The whole point: a partial Gemini answer must reach Groq."""
    monkeypatch.setattr(ai_provider, "_gemini_chat",
                        lambda *a, **k: (_ for _ in ()).throw(AITruncatedError("partial")))
    called = {}

    def fake_groq(messages, tier, temperature, max_tokens, response_format):
        called["yes"] = True
        return '{"schedule": [{"blocks": [1]}]}'

    monkeypatch.setattr(ai_provider, "_groq_chat", fake_groq)
    out = ai_provider.chat(MSGS, max_tokens=8000)
    assert called.get("yes") is True
    assert "schedule" in out


def test_groq_length_truncation_also_raises(both_keys, monkeypatch):
    class _Choice:
        finish_reason = "length"
        message = type("M", (), {"content": '{"partial": tr'})()

    monkeypatch.setattr(ai_provider, "_groq_client",
                        lambda: type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {
                            "create": staticmethod(lambda **kw: type("R", (), {"choices": [_Choice()]})())})()})()})())
    with pytest.raises(AITruncatedError):
        ai_provider._groq_chat(MSGS, "standard", 0.3, 100, None)


# ── thinking budget ───────────────────────────────────────────────


@pytest.mark.parametrize("model,expected", [
    ("gemini-2.5-flash", True),
    ("gemini-3.0-pro", True),
    ("gemini-2.0-flash-lite", False),
    ("gemini-1.5-pro", False),
    ("", False),
])
def test_thinking_support_is_detected_by_model_family(model, expected):
    assert ai_provider._supports_thinking(model) is expected


def test_thinking_budget_is_capped_by_default():
    # The uncapped default is what let reasoning eat the output budget.
    assert ai_provider.DEFAULT_THINKING_BUDGET >= 0


# ── transient retry ───────────────────────────────────────────────


@pytest.mark.parametrize("msg,expected", [
    ("503 UNAVAILABLE high demand", True),
    ("model is overloaded", True),
    ("500 internal error", True),
    ("429 RESOURCE_EXHAUSTED", False),
    ("invalid api key", False),
])
def test_transient_errors_are_classified(msg, expected):
    assert ai_provider._is_transient_error(Exception(msg)) is expected


def test_a_transient_error_is_retried_once_before_falling_back(gemini_only, monkeypatch):
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return "recovered"

    monkeypatch.setattr(ai_provider, "_gemini_chat", flaky)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda s: None)
    assert ai_provider.chat(MSGS) == "recovered"
    assert len(calls) == 2


def test_truncation_is_not_retried_on_the_same_provider(gemini_only, monkeypatch):
    calls = []

    def always_truncated(*a, **k):
        calls.append(1)
        raise AITruncatedError("partial")

    monkeypatch.setattr(ai_provider, "_gemini_chat", always_truncated)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        ai_provider.chat(MSGS)
    assert len(calls) == 1, "retrying an over-budget request just burns quota"


def test_no_backend_configured_raises_clearly(no_keys):
    with pytest.raises(RuntimeError, match="No AI backend available"):
        ai_provider.chat(MSGS)
