"""Webhook de Twilio (Sandbox de WhatsApp): mensajes entrantes y avisos de entrega."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_session_factory
from app.models import Customer, Message
from app.routers.webhook import _apply_statuses, _process
from app.services.inbox import store_incoming
from app.services.whatsapp import get_whatsapp_client
from app.services.whatsapp.base import IncomingMessage, WhatsAppClient
from app.services.whatsapp.twilio import parse_form, valid_signature
from app.services.whatsapp.webhook_parser import StatusEvent

log = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
ERROR_TITLES = {63016: "fuera de las 24 h", 63015: "no se unió al Sandbox", 63003: "número inválido"}


def _signed_url(request: Request) -> str:
    """La URL exacta que Twilio firmó: la pública configurada, o la que llegó (con proxy-headers)."""
    base = get_settings().public_url.rstrip("/")
    if not base:
        return str(request.url)
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{query}"


async def _verified_form(request: Request) -> dict[str, str] | None:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not valid_signature(_signed_url(request), form, request.headers.get("x-twilio-signature"), get_settings().twilio_auth_token):
        log.warning("Webhook de Twilio con firma inválida rechazado")
        return None
    return form


def resolve_numbered_reply(db, msg: IncomingMessage) -> IncomingMessage:
    """'2' después de un mensaje con opciones = tocar el 2.º botón (así el bot recibe lo mismo que con Meta)."""
    choice = msg.text.strip()
    if msg.type != "text" or choice not in ("1", "2", "3"):
        return msg
    customer = db.scalar(select(Customer).where(Customer.phone == msg.phone))
    if customer is None:
        return msg
    last_out = db.scalar(
        select(Message).where(Message.customer_id == customer.id, Message.direction == "out").order_by(Message.id.desc()).limit(1)
    )
    buttons = ((last_out.payload_json or {}).get("buttons") or []) if last_out else []
    if int(choice) > len(buttons):
        return msg
    button = buttons[int(choice) - 1]
    return IncomingMessage(
        phone=msg.phone, type="button", text=button["title"], button_id=button["id"], wa_message_id=msg.wa_message_id, name=msg.name
    )


def _store(factory: sessionmaker, msg: IncomingMessage) -> int | None:
    with factory() as db:
        inbound = store_incoming(db, resolve_numbered_reply(db, msg))
        return inbound.customer_id if inbound is not None and inbound.processed_at is None else None


@router.post("/webhook")
async def receive(
    request: Request,
    background: BackgroundTasks,
    factory: sessionmaker = Depends(get_session_factory),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    form = await _verified_form(request)
    if form is None:
        return PlainTextResponse("Invalid signature", status_code=403)
    msg = parse_form(form)
    if not msg.phone:
        return Response(EMPTY_TWIML, media_type="text/xml")
    try:
        customer_id = await run_in_threadpool(_store, factory, msg)
    except Exception:
        # Twilio reintenta si no recibe 2xx: mejor 500 que perder el mensaje [H4].
        log.exception("No se pudo guardar el webhook de Twilio")
        return PlainTextResponse("Error", status_code=500)
    if customer_id is not None:
        background.add_task(_process, factory, client, {customer_id})
    # TwiML vacío: Twilio no responde nada por su cuenta; el bot contesta por la API.
    return Response(EMPTY_TWIML, media_type="text/xml")


def _apply(factory: sessionmaker, events: list[StatusEvent]) -> None:
    with factory() as db:
        _apply_statuses(db, events)


@router.post("/status")
async def status(request: Request, factory: sessionmaker = Depends(get_session_factory)):
    """StatusCallback: un 'failed'/'undelivered' marca el mensaje con error en el panel."""
    form = await _verified_form(request)
    if form is None:
        return PlainTextResponse("Invalid signature", status_code=403)
    if form.get("MessageStatus") in ("failed", "undelivered") and form.get("MessageSid"):
        code = int(form["ErrorCode"]) if form.get("ErrorCode", "").isdigit() else None
        event = StatusEvent(form["MessageSid"], "failed", code, ERROR_TITLES.get(code))
        await run_in_threadpool(_apply, factory, [event])
    return PlainTextResponse("")
