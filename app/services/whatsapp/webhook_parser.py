"""Payload del webhook de la Cloud API → mensajes y estados normalizados.

Formato verificado en la documentación oficial (2026-10):
entry[].changes[].value.{metadata, contacts[], messages[], statuses[]}.
"""

import logging
from dataclasses import dataclass, field

from app.services.whatsapp.base import IncomingMessage

log = logging.getLogger(__name__)

MEDIA_TYPES = {"image", "audio", "video", "document", "sticker", "location", "contacts"}
# Subtipos de "unsupported" que no son algo que el cliente escribió: se ignoran.
IGNORED_UNSUPPORTED = {"reaction", "pin", "keep_in_chat", "poll_update", "errors", "hsm", "link_preview"}


@dataclass
class StatusEvent:
    wa_message_id: str
    status: str  # sent | delivered | read | played | failed
    error_code: int | None = None
    error_title: str | None = None


@dataclass
class WebhookBatch:
    messages: list[IncomingMessage] = field(default_factory=list)
    statuses: list[StatusEvent] = field(default_factory=list)


def _phone(wa_id: str) -> str:
    digits = "".join(c for c in wa_id if c.isdigit())
    return "+" + digits


def _to_incoming(m: dict, names: dict[str, str]) -> IncomingMessage:
    kind = m.get("type", "unknown")
    base = dict(phone=_phone(m.get("from", "")), wa_message_id=m.get("id"), name=names.get(m.get("from", "")))

    if kind == "text":
        return IncomingMessage(type="text", text=(m.get("text") or {}).get("body", ""), **base)
    if kind == "interactive":
        inter = m.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        if reply:
            return IncomingMessage(type="button", text=reply.get("title", ""), button_id=reply.get("id"), **base)
        return IncomingMessage(type="unknown", **base)
    if kind == "button":
        # Respuesta rápida de plantilla: no usamos plantillas, se trata como texto.
        return IncomingMessage(type="text", text=(m.get("button") or {}).get("text", ""), **base)
    if kind == "reaction":
        return IncomingMessage(type="reaction", **base)
    if kind in MEDIA_TYPES:
        return IncomingMessage(type=kind, **base)
    if kind == "unsupported":
        sub = (m.get("unsupported") or {}).get("type", "")
        if sub == "edit":
            return IncomingMessage(type="edit", **base)
        if sub in IGNORED_UNSUPPORTED:
            return IncomingMessage(type="system", **base)
        if sub in MEDIA_TYPES or sub == "gif":
            return IncomingMessage(type="image" if sub == "gif" else sub, **base)
        return IncomingMessage(type="unknown", **base)
    if kind in ("system", "ephemeral", "request_welcome", "order"):
        return IncomingMessage(type="system", **base)
    return IncomingMessage(type="unknown", **base)


def parse_webhook(payload: dict, phone_number_id: str | None = None) -> WebhookBatch:
    batch = WebhookBatch()
    if payload.get("object") != "whatsapp_business_account":
        return batch
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            meta_id = (value.get("metadata") or {}).get("phone_number_id")
            if phone_number_id and meta_id and meta_id != phone_number_id:
                log.warning("Webhook de otro número (%s), ignorado", meta_id)
                continue
            names = {
                c.get("wa_id", ""): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
            }
            for m in value.get("messages") or []:
                if m.get("from") and m.get("id"):
                    batch.messages.append(_to_incoming(m, names))
            for st in value.get("statuses") or []:
                err = (st.get("errors") or [{}])[0]
                batch.statuses.append(
                    StatusEvent(
                        wa_message_id=st.get("id", ""),
                        status=st.get("status", ""),
                        error_code=err.get("code"),
                        error_title=err.get("title"),
                    )
                )
    return batch
