"""Webhook de la WhatsApp Business Cloud API (verificación + mensajes entrantes)."""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_session_factory
from app.models import Message
from app.services.events import broker
from app.services.inbox import process_pending, store_incoming
from app.services.outbox import OUTSIDE_WINDOW_ERROR
from app.services.whatsapp import get_whatsapp_client
from app.services.whatsapp.base import WhatsAppClient
from app.services.whatsapp.cloud import OUTSIDE_WINDOW_CODES
from app.services.whatsapp.webhook_parser import StatusEvent, WebhookBatch, parse_webhook

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


def valid_signature(body: bytes, header: str | None, secret: str) -> bool:
    """X-Hub-Signature-256: sha256=<HMAC-SHA256(body, App Secret)>."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@router.get("/webhook")
def verify(request: Request):
    params = request.query_params
    token = get_settings().wa_verify_token
    if (
        params.get("hub.mode") == "subscribe"
        and token
        and hmac.compare_digest(params.get("hub.verify_token", ""), token)
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)


def _store(factory: sessionmaker, batch: WebhookBatch) -> set[int]:
    """Guarda entrantes y aplica estados antes de responder 200 [H4]. Devuelve clientes a procesar."""
    customers = set()
    with factory() as db:
        for msg in batch.messages:
            inbound = store_incoming(db, msg)
            if inbound is not None and inbound.processed_at is None:
                customers.add(inbound.customer_id)
        _apply_statuses(db, batch.statuses)
    return customers


def _apply_statuses(db, statuses: list[StatusEvent]) -> None:
    """Un 'failed' de Meta marca el mensaje con error para que el tendero lo vea."""
    for st in statuses:
        if st.status != "failed" or not st.wa_message_id:
            continue
        msg = db.scalar(select(Message).where(Message.wa_message_id == st.wa_message_id))
        if msg is None:
            continue
        if st.error_code in OUTSIDE_WINDOW_CODES:
            msg.send_error = OUTSIDE_WINDOW_ERROR
        else:
            msg.send_error = f"No entregado ({st.error_code}): {st.error_title or 'error'}"[:255]
        db.commit()
        broker.publish("status_changed", order_id=msg.order_id)


def _process(factory: sessionmaker, client: WhatsAppClient, customer_ids: set[int]) -> None:
    for cid in customer_ids:
        try:
            with factory() as db:
                process_pending(db, client, cid)
        except Exception:
            # Queda pendiente (processed_at nulo) y se reintenta al reiniciar [H3].
            log.exception("Error procesando pendientes del cliente %s", cid)


@router.post("/webhook")
async def receive(
    request: Request,
    background: BackgroundTasks,
    factory: sessionmaker = Depends(get_session_factory),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    body = await request.body()
    settings = get_settings()
    if not valid_signature(body, request.headers.get("x-hub-signature-256"), settings.wa_app_secret):
        log.warning("Webhook con firma inválida rechazado")
        return PlainTextResponse("Invalid signature", status_code=403)
    try:
        payload = json.loads(body)
    except ValueError:
        return PlainTextResponse("Bad JSON", status_code=400)

    batch = parse_webhook(payload, settings.wa_phone_number_id or None)
    try:
        customer_ids = await run_in_threadpool(_store, factory, batch)
    except Exception:
        # Si la DB no responde, Meta reintenta durante 36 h: mejor 500 que perder el mensaje [H4].
        log.exception("No se pudo guardar el webhook; Meta lo reintentará")
        return JSONResponse({"ok": False}, status_code=500)

    if customer_ids:
        background.add_task(_process, factory, client, customer_ids)
    return {"ok": True}
