"""Cliente de WhatsApp vía Twilio (Sandbox), solo para probar el MVP sin número propio en Meta.

Las respuestas del bot viajan como TwiML en la respuesta del webhook (TwimlReplies): el número
de prueba de la consola nueva rechaza texto libre por la API (error 21654) pero sí entrega TwiML.
Lo que el tendero envía desde el panel sigue usando la API.

Formato verificado en la documentación oficial de Twilio (2026-10):
- Envío: POST /2010-04-01/Accounts/{SID}/Messages.json (form, basic auth) con From/To "whatsapp:+E164".
- Entrantes: webhook form-urlencoded (From, Body, MessageSid, ProfileName, NumMedia, ButtonPayload…).
- Firma X-Twilio-Signature: base64(HMAC-SHA1(auth_token, URL + nombre/valor de cada parámetro ordenados)).

El Sandbox no garantiza botones interactivos: se envían como opciones numeradas y la respuesta
"1", "2" o "3" se traduce al botón en el webhook.
"""

import base64
import hashlib
import hmac
import logging
import time
import uuid
from collections.abc import Mapping
from xml.sax.saxutils import escape

import httpx

from app.config import get_settings
from app.services.whatsapp.base import IncomingMessage, OutgoingMessage, SendResult
from app.services.whatsapp.cloud import BACKOFF, health
from app.utils import mask_phone

log = logging.getLogger(__name__)

MAX_BODY = 1600  # límite de Body en WhatsApp vía Twilio
OUTSIDE_WINDOW_CODES = {63016}  # fuera de las 24 h: requeriría plantilla
NOT_JOINED_CODES = {63015}  # el número no se unió al Sandbox
AUTH_CODES = {20003}  # credenciales inválidas
NUMBERS = ["1️⃣", "2️⃣", "3️⃣"]


def render_text(message: OutgoingMessage) -> str:
    """Los botones se vuelven opciones numeradas."""
    if not message.buttons:
        return message.text
    options = "\n".join(f"{NUMBERS[i]} {b.title}" for i, b in enumerate(message.buttons))
    return f"{message.text}\n\n{options}\n_Responde con el número_"


def signature(url: str, params: Mapping[str, str], auth_token: str) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def valid_signature(url: str, params: Mapping[str, str], header: str | None, auth_token: str) -> bool:
    if not auth_token or not header:
        return False
    return hmac.compare_digest(signature(url, params, auth_token), header)


def _phone(value: str) -> str:
    return value.removeprefix("whatsapp:")


def parse_form(form: Mapping[str, str]) -> IncomingMessage:
    """Webhook de Twilio → el mismo IncomingMessage que produce el de Meta."""
    common = {
        "phone": _phone(form.get("From", "")),
        "wa_message_id": form.get("MessageSid") or None,
        "name": form.get("ProfileName") or None,
    }
    if form.get("ButtonPayload"):
        return IncomingMessage(type="button", button_id=form["ButtonPayload"], text=form.get("ButtonText", ""), **common)
    if int(form.get("NumMedia") or 0) > 0:
        kind = (form.get("MediaContentType0") or "").split("/")[0]
        if kind == "application":
            kind = "document"
        return IncomingMessage(type=kind if kind in ("image", "audio", "video", "document") else "unknown", **common)
    if form.get("Latitude"):
        return IncomingMessage(type="location", **common)
    return IncomingMessage(type="text", text=form.get("Body", ""), **common)


def split_body(text: str) -> list[str]:
    from app.services.outbox import split_text  # evita import circular

    return split_text(text, MAX_BODY)


class TwilioWhatsAppClient:
    def __init__(self, account_sid: str, auth_token: str, from_: str, status_callback: str = "", client: httpx.Client | None = None, sleep=time.sleep):
        self.url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        self.auth = (account_sid, auth_token)
        self.from_ = from_
        self.status_callback = status_callback
        self.client = client or httpx.Client(timeout=10.0)
        self.sleep = sleep

    @classmethod
    def from_settings(cls) -> "TwilioWhatsAppClient":
        s = get_settings()
        callback = f"{s.public_url.rstrip('/')}/twilio/status" if s.public_url else ""
        return cls(s.twilio_account_sid, s.twilio_auth_token, s.twilio_from, callback)

    def send(self, to: str, message: OutgoingMessage) -> SendResult:
        result = SendResult(ok=False, error="Sin intentar")
        for chunk in split_body(render_text(message)):
            result = self._send_with_retries(to, chunk)
            if not result.ok:
                break
        return result

    def _send_with_retries(self, to: str, body: str) -> SendResult:
        data = {"From": self.from_, "To": f"whatsapp:{to}", "Body": body}
        if self.status_callback:
            data["StatusCallback"] = self.status_callback
        attempts = len(BACKOFF) + 1
        result = SendResult(ok=False, error="Sin intentar")
        for attempt in range(attempts):
            result, retry = self._send_once(to, data)
            if result.ok or not retry or attempt == attempts - 1:
                break
            self.sleep(BACKOFF[attempt])
        return result

    def _send_once(self, to: str, data: dict) -> tuple[SendResult, bool]:
        try:
            resp = self.client.post(self.url, data=data, auth=self.auth)
        except httpx.HTTPError as exc:
            log.warning("Error de red enviando a %s: %s", mask_phone(to), type(exc).__name__)
            return SendResult(ok=False, error="Sin conexión con Twilio"), True

        if resp.status_code < 300:
            health.mark_ok()
            return SendResult(ok=True, wa_message_id=resp.json().get("sid")), False

        try:
            err = resp.json()
        except ValueError:
            err = {}
        code = err.get("code")
        detail = f"Twilio {code}: {err.get('message', resp.status_code)}"[:255]
        log.warning("Envío fallido a %s: %s", mask_phone(to), detail)

        if code in OUTSIDE_WINDOW_CODES:
            return SendResult(ok=False, error=detail, outside_window=True), False
        if code in NOT_JOINED_CODES:
            return SendResult(ok=False, error="El cliente no se ha unido al Sandbox de Twilio (debe enviar 'join …')"), False
        if code in AUTH_CODES or resp.status_code == 401:
            health.mark_auth_error(detail)
            return SendResult(ok=False, error="Credenciales de Twilio inválidas"), False
        return SendResult(ok=False, error=detail), resp.status_code >= 500 or resp.status_code == 429


class TwimlReplies:
    """Cliente que junta las respuestas para el remitente del webhook y las devuelve como TwiML.

    Lo dirigido a otro número (p. ej. el aviso "eres el siguiente") se envía con el cliente real.
    """

    def __init__(self, phone: str, fallback):
        self.phone = phone
        self.fallback = fallback
        self.bodies: list[str] = []

    def send(self, to: str, message: OutgoingMessage) -> SendResult:
        if to != self.phone:
            return self.fallback.send(to, message)
        self.bodies.extend(split_body(render_text(message)))
        # Id propio para que el contador del panel lo cuente como enviado.
        return SendResult(ok=True, wa_message_id=f"twiml-{uuid.uuid4().hex}")

    def twiml(self) -> str:
        messages = "".join(f"<Message>{escape(b)}</Message>" for b in self.bodies)
        return f'<?xml version="1.0" encoding="UTF-8"?><Response>{messages}</Response>'
