from datetime import timedelta

from sqlalchemy.orm import Session

from app.flow import texts
from app.models import Customer, Message, Order
from app.services.whatsapp.base import OutgoingMessage, WhatsAppClient
from app.utils import as_utc, utcnow

WINDOW = timedelta(hours=24)
OUTSIDE_WINDOW_ERROR = "El cliente debe escribir primero para poder responderle"

# Límites de la Cloud API.
MAX_TEXT = 4096
MAX_INTERACTIVE_BODY = 1024


def within_window(customer: Customer) -> bool:
    return customer.last_inbound_at is not None and utcnow() - as_utc(customer.last_inbound_at) < WINDOW


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Parte por líneas (o por espacios si una línea es enorme) sin pasar del límite."""
    pieces = []
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        pieces.append(line)
    chunks, current = [], None
    for piece in pieces:
        candidate = piece if current is None else f"{current}\n{piece}"
        if len(candidate) > limit:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    chunks.append(current or "")
    return chunks


def fit_limits(out: OutgoingMessage) -> list[OutgoingMessage]:
    """Ajusta un mensaje a los límites de WhatsApp [H8]."""
    if out.buttons and len(out.text) > MAX_INTERACTIVE_BODY:
        return [OutgoingMessage(t) for t in split_text(out.text)] + [
            OutgoingMessage(texts.CHOOSE_OPTION, out.buttons)
        ]
    if len(out.text) > MAX_TEXT:
        return [OutgoingMessage(t) for t in split_text(out.text)]
    return [out]


def send_and_log(
    db: Session,
    client: WhatsAppClient,
    customer: Customer,
    order: Order | None,
    out: OutgoingMessage,
    sender: str,
) -> Message:
    """Envía (solo mensajes de servicio, dentro de la ventana de 24h) y deja registro.

    Devuelve el último registro guardado (un mensaje largo puede partirse en varios).
    """
    record = None
    for part in fit_limits(out):
        record = _send_one(db, client, customer, order, part, sender)
    return record


def _send_one(db, client, customer, order, out: OutgoingMessage, sender: str) -> Message:
    record = Message(
        order_id=order.id if order else None,
        customer_id=customer.id,
        direction="out",
        sender=sender,
        text=out.text,
        payload_json={"buttons": [{"id": b.id, "title": b.title} for b in out.buttons]} if out.buttons else None,
    )
    if not within_window(customer):
        # Iniciar conversaciones requiere plantillas pagas: no lo hacemos.
        record.send_error = OUTSIDE_WINDOW_ERROR
    else:
        result = client.send(customer.phone, out)
        if result.ok:
            record.wa_message_id = result.wa_message_id
        else:
            record.send_error = OUTSIDE_WINDOW_ERROR if result.outside_window else (result.error or "Error enviando")[:255]
    db.add(record)
    db.flush()
    return record
