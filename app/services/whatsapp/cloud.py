"""Cliente real de la WhatsApp Business Cloud API (Graph API). Solo mensajes de servicio.

Formato verificado en la documentación oficial (2026-10): POST /{version}/{phone_number_id}/messages.
"""

import logging
import threading
import time

import httpx

from app.config import get_settings
from app.services.whatsapp.base import OutgoingMessage, SendResult
from app.utils import mask_phone, utcnow

log = logging.getLogger(__name__)

OUTSIDE_WINDOW_CODES = {131047}  # el cliente no ha escrito en 24 h
AUTH_CODES = {190, 0}  # token vencido o inválido
RETRY_CODES = {1, 2, 130429, 131000, 131016, 131056, 80007}
BACKOFF = (0.5, 1.5)  # dos reintentos [H5]


class WhatsAppHealth:
    """Estado de la conexión con Meta para mostrarlo en el panel [H6]."""

    def __init__(self):
        self._lock = threading.Lock()
        self.auth_error: str | None = None
        self.auth_error_at = None

    def mark_auth_error(self, detail: str) -> None:
        with self._lock:
            self.auth_error, self.auth_error_at = detail, utcnow()

    def mark_ok(self) -> None:
        with self._lock:
            self.auth_error, self.auth_error_at = None, None


health = WhatsAppHealth()


class CloudWhatsAppClient:
    def __init__(self, phone_number_id: str, access_token: str, version: str, client: httpx.Client | None = None, sleep=time.sleep):
        self.url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
        self.client = client or httpx.Client(timeout=10.0)
        self.headers = {"Authorization": f"Bearer {access_token}"}
        self.sleep = sleep

    @classmethod
    def from_settings(cls) -> "CloudWhatsAppClient":
        s = get_settings()
        return cls(s.wa_phone_number_id, s.wa_access_token, s.wa_graph_version)

    @staticmethod
    def payload(to: str, message: OutgoingMessage) -> dict:
        base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+")}
        if not message.buttons:
            return {**base, "type": "text", "text": {"body": message.text, "preview_url": False}}
        return {
            **base,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": message.text},
                "action": {
                    "buttons": [{"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in message.buttons]
                },
            },
        }

    def send(self, to: str, message: OutgoingMessage) -> SendResult:
        body = self.payload(to, message)
        attempts = len(BACKOFF) + 1
        result = SendResult(ok=False, error="Sin intentar")
        for attempt in range(attempts):
            result, retry = self._send_once(to, body)
            if result.ok or not retry or attempt == attempts - 1:
                break
            self.sleep(BACKOFF[attempt])
        return result

    def _send_once(self, to: str, body: dict) -> tuple[SendResult, bool]:
        try:
            resp = self.client.post(self.url, json=body, headers=self.headers)
        except httpx.HTTPError as exc:
            log.warning("Error de red enviando a %s: %s", mask_phone(to), type(exc).__name__)
            return SendResult(ok=False, error="Sin conexión con WhatsApp"), True

        if resp.status_code < 300:
            health.mark_ok()
            messages = resp.json().get("messages") or [{}]
            return SendResult(ok=True, wa_message_id=messages[0].get("id")), False

        try:
            err = resp.json().get("error") or {}
        except ValueError:
            err = {}
        code = err.get("code")
        detail = f"WhatsApp {code}: {err.get('message', resp.status_code)}"[:255]
        log.warning("Envío fallido a %s: %s", mask_phone(to), detail)

        if code in OUTSIDE_WINDOW_CODES:
            return SendResult(ok=False, error=detail, outside_window=True), False
        if code in AUTH_CODES or resp.status_code == 401:
            health.mark_auth_error(detail)
            return SendResult(ok=False, error="Token de WhatsApp inválido o vencido"), False
        retry = code in RETRY_CODES or resp.status_code >= 500 or resp.status_code == 429
        return SendResult(ok=False, error=detail), retry
