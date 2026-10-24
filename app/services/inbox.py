"""Punto de entrada único para mensajes entrantes (webhook de Meta y simulador)."""

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.flow import texts
from app.flow.bot import NON_TEXT_LABEL, Bot, BotResult, get_active_order
from app.models import Customer, Message
from app.services.events import broker
from app.services.extractor import Extractor, build_extractor
from app.services.orders import notify_next_in_line
from app.services.outbox import send_and_log
from app.services.whatsapp.base import IncomingMessage, OutgoingMessage, WhatsAppClient
from app.utils import as_utc, mask_phone, utcnow

log = logging.getLogger(__name__)

# Serializa los mensajes de un mismo teléfono (un solo proceso).
_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
# Marcas de tiempo recientes por teléfono, para el límite anti-spam [H9].
_recent: defaultdict[str, deque] = defaultdict(deque)


def _rate_limited(phone: str) -> bool:
    now = time.monotonic()
    q = _recent[phone]
    while q and now - q[0] > 60:
        q.popleft()
    q.append(now)
    return len(q) > get_settings().rate_limit_per_minute


def _get_or_create_customer(db: Session, phone: str, name: str | None) -> Customer:
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        customer = Customer(phone=phone, name=name)
        db.add(customer)
        db.flush()
    elif name and not customer.name:
        customer.name = name
    return customer


def _inbound_text(msg: IncomingMessage) -> str:
    if msg.type == "text":
        return msg.text
    if msg.type == "button":
        return msg.text or msg.button_id or ""
    return NON_TEXT_LABEL.get(msg.type, NON_TEXT_LABEL["unknown"])


def store_incoming(db: Session, msg: IncomingMessage, now: datetime | None = None) -> Message | None:
    """Guarda el entrante en su propia transacción [H2/H4]. None si es un duplicado de Meta [H1].

    Es rápido (una escritura) para que el webhook pueda hacerlo antes de responder 200.
    """
    now = now or utcnow()
    with _locks[msg.phone]:
        customer = _get_or_create_customer(db, msg.phone, msg.name)
        inbound = Message(
            customer_id=customer.id,
            direction="in",
            sender="cliente",
            text=_inbound_text(msg),
            wa_message_id=msg.wa_message_id,
            payload_json={"type": msg.type, "button_id": msg.button_id},
            created_at=now,
        )
        try:
            with db.begin_nested():
                db.add(inbound)
                db.flush()
        except IntegrityError:
            # El savepoint ya se deshizo; el resto de la transacción queda intacto.
            log.info("Mensaje duplicado ignorado: %s", msg.wa_message_id)
            db.commit()
            return None
        customer.last_inbound_at = now
        if _rate_limited(msg.phone):  # [H9] se guarda pero no se responde
            log.warning("Límite de mensajes superado por %s; no se responde", mask_phone(msg.phone))
            order = get_active_order(db, customer)
            inbound.order_id = order.id if order else None
            inbound.processed_at = utcnow()
        db.commit()
        return inbound


def process_pending(
    db: Session, client: WhatsAppClient, customer_id: int, extractor: Extractor | None = None
) -> int:
    """Procesa, en orden de llegada, los entrantes pendientes de un cliente [H3].

    Si dos webhooks del mismo cliente llegan casi juntos, el que corra primero procesa ambos
    en orden y el otro no encuentra nada pendiente.
    """
    customer = db.get(Customer, customer_id)
    with _locks[customer.phone]:
        pending = list(
            db.scalars(
                select(Message)
                .where(
                    Message.customer_id == customer_id,
                    Message.direction == "in",
                    Message.processed_at.is_(None),
                )
                .order_by(Message.id)
            )
        )
        results = []
        for inbound in pending:
            payload = inbound.payload_json or {}
            msg = IncomingMessage(
                phone=customer.phone,
                type=payload.get("type", "text"),
                text=inbound.text,
                button_id=payload.get("button_id"),
                wa_message_id=inbound.wa_message_id,
            )
            results.append(run_bot(db, customer, inbound, msg, client, extractor, as_utc(inbound.created_at)))
    for result in results:
        _publish(customer.phone, result)
    return len(pending)


def process_incoming(
    db: Session,
    msg: IncomingMessage,
    client: WhatsAppClient,
    extractor: Extractor | None = None,
    now: datetime | None = None,
) -> bool:
    """Guardar y procesar en el mismo hilo (simulador y tests). False si era duplicado."""
    inbound = store_incoming(db, msg, now)
    if inbound is None:
        return False
    if inbound.processed_at is not None:  # anti-spam: guardado pero sin respuesta
        broker.publish("sim_message", phone=msg.phone)
        return True
    process_pending(db, client, inbound.customer_id, extractor)
    return True


def run_bot(
    db: Session,
    customer: Customer,
    inbound: Message,
    msg: IncomingMessage,
    client: WhatsAppClient,
    extractor: Extractor | None,
    now: datetime,
) -> BotResult | None:
    """Corre el bot sobre un entrante ya guardado. Si algo falla, se disculpa y deja log."""
    try:
        result = Bot(extractor or build_extractor()).handle(db, customer, inbound, msg, now)
        for reply in result.replies:
            send_and_log(db, client, customer, result.order, reply, sender="bot")
        if result.status_changed:
            notify_next_in_line(db, client)
        inbound.processed_at = utcnow()
        db.commit()
        return result
    except Exception:
        log.exception("Error procesando mensaje de %s", mask_phone(customer.phone))
        db.rollback()
        try:
            send_and_log(db, client, customer, None, OutgoingMessage(texts.APOLOGY), sender="bot")
            inbound.processed_at = utcnow()
            db.commit()
        except Exception:
            log.exception("Tampoco se pudo enviar la disculpa")
            db.rollback()
        return None


def reprocess_pending(db: Session, client: WhatsAppClient, extractor: Extractor | None = None) -> int:
    """Al arrancar: procesa lo que quedó sin terminar (p. ej. reinicio a mitad de camino) [H3]."""
    customer_ids = db.scalars(
        select(Message.customer_id)
        .where(Message.direction == "in", Message.processed_at.is_(None))
        .group_by(Message.customer_id)
    ).all()
    return sum(process_pending(db, client, cid, extractor) for cid in customer_ids)


def _publish(phone: str, result: BotResult | None) -> None:
    broker.publish("sim_message", phone=phone)
    if result is None:
        return
    order_id = result.order.id if result.order else None
    if result.new_order_queued:
        broker.publish("new_order", order_id=order_id)
    elif result.status_changed:
        broker.publish("status_changed", order_id=order_id)
    if result.human_message:
        broker.publish("new_message", order_id=order_id)
