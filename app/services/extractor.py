"""Extracción de dirección y monto. Reglas por defecto; Gemini (tier gratis) opcional."""

import json
import logging
import re
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.services import parsers

log = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract_address(self, text: str) -> tuple[int, str] | None: ...
    def extract_amount(self, text: str) -> int | None: ...


class RuleBasedExtractor:
    def extract_address(self, text: str) -> tuple[int, str] | None:
        return parsers.parse_address(text)

    def extract_amount(self, text: str) -> int | None:
        return parsers.parse_amount(text)


class GeminiExtractor:
    """Solo se usa cuando las reglas no entienden. Cualquier fallo lanza excepción."""

    _URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=5.0)

    def _ask(self, instruction: str, text: str) -> dict:
        resp = self.client.post(
            self._URL.format(model=self.model),
            headers={"x-goog-api-key": self.api_key},
            json={
                "contents": [{"parts": [{"text": f"{instruction}\n\nMensaje: {text!r}"}]}],
            },
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # No dependemos del modo JSON (su sintaxis cambia entre versiones): extraemos el objeto.
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError(f"respuesta sin JSON: {raw[:80]!r}")
        return json.loads(match.group())

    def extract_address(self, text: str) -> tuple[int, str] | None:
        data = self._ask(
            "Un residente de un conjunto de torres escribió su dirección de entrega. "
            'Responde SOLO JSON {"torre": <entero o null>, "apto": "<3-4 dígitos o null>"}.',
            text,
        )
        torre, apto = data.get("torre"), data.get("apto")
        if torre is None or apto is None:
            return None
        # Mismas reglas de rango que el parser.
        return parsers.parse_address(f"torre {int(torre)} apto {str(apto).strip()}")

    def extract_amount(self, text: str) -> int | None:
        data = self._ask(
            "Un cliente dice con cuánto dinero en efectivo (pesos colombianos) va a pagar. "
            'Responde SOLO JSON {"monto": <entero en pesos o null>}.',
            text,
        )
        monto = data.get("monto")
        if monto is None:
            return None
        monto = int(monto)
        return monto if 0 < monto < 10_000_000 else None


class FallbackExtractor:
    """Primero reglas; si no entienden, el secundario. Si el secundario falla → None."""

    def __init__(self, primary: Extractor, secondary: Extractor | None):
        self.primary = primary
        self.secondary = secondary

    def _try(self, method: str, text: str):
        if (result := getattr(self.primary, method)(text)) is not None:
            return result
        if self.secondary is None:
            return None
        try:
            return getattr(self.secondary, method)(text)
        except Exception as exc:  # cuota agotada, timeout, JSON inválido…
            log.warning("LLM falló en %s, uso solo reglas: %s", method, exc)
            return None

    def extract_address(self, text: str) -> tuple[int, str] | None:
        return self._try("extract_address", text)

    def extract_amount(self, text: str) -> int | None:
        return self._try("extract_amount", text)


def build_extractor(settings: Settings | None = None) -> Extractor:
    settings = settings or get_settings()
    secondary = None
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        secondary = GeminiExtractor(settings.gemini_api_key, settings.gemini_model)
    return FallbackExtractor(RuleBasedExtractor(), secondary)
