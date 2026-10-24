import httpx
import pytest

from app.services.extractor import FallbackExtractor, GeminiExtractor, RuleBasedExtractor


def _gemini(handler) -> GeminiExtractor:
    return GeminiExtractor("fake-key", "gemini-test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _answer(text: str):
    return lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_rules_first_llm_not_called():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500)

    ex = FallbackExtractor(RuleBasedExtractor(), _gemini(handler))
    assert ex.extract_address("t3 502") == (3, "502")
    assert calls == []


def test_gemini_understands_hard_address():
    ex = FallbackExtractor(RuleBasedExtractor(), _gemini(_answer('```json\n{"torre": 4, "apto": "302"}\n```')))
    assert ex.extract_address("pa la 4, el 302") == (4, "302")


def test_gemini_result_still_validated():
    ex = FallbackExtractor(RuleBasedExtractor(), _gemini(_answer('{"torre": 99, "apto": "302"}')))
    assert ex.extract_address("pa la 99, el 302") is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda req: httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
        lambda req: httpx.Response(500),
        _answer("no sé"),
        lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("timeout")),
    ],
    ids=["cuota", "error_500", "json_invalido", "timeout"],
)
def test_gemini_failure_falls_back_to_rules(handler):
    ex = FallbackExtractor(RuleBasedExtractor(), _gemini(handler))
    assert ex.extract_address("pa la 4, el 302") is None
    assert ex.extract_amount("veinte mil") is None
    # Las reglas siguen funcionando aunque Gemini esté caído.
    assert ex.extract_amount("20k") == 20_000


def test_gemini_amount():
    ex = FallbackExtractor(RuleBasedExtractor(), _gemini(_answer('{"monto": 20000}')))
    assert ex.extract_amount("veinte mil") == 20_000


def test_without_llm():
    ex = FallbackExtractor(RuleBasedExtractor(), None)
    assert ex.extract_address("pa la 4, el 302") is None
