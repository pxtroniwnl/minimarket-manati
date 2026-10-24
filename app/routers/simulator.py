"""Simulador de WhatsApp para probar el bot sin Meta (solo con WHATSAPP_MODE=mock)."""

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Message
from app.services.auth import require_panel
from app.services.events import broker
from app.services.inbox import process_incoming
from app.services.whatsapp import get_whatsapp_client
from app.services.whatsapp.base import IncomingMessage, WhatsAppClient
from app.templating import templates

# Pide el login del panel: desplegado en una URL pública, nadie más debe poder crear pedidos.
router = APIRouter(prefix="/simulador", tags=["simulador"], dependencies=[Depends(require_panel)])

DEFAULT_PHONES = ["+573001110001", "+573001110002", "+573001110003"]


def _normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:  # celular colombiano sin indicativo
        digits = "57" + digits
    return "+" + digits


def _chat(db: Session, phone: str) -> list[Message]:
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        return []
    return list(db.scalars(select(Message).where(Message.customer_id == customer.id).order_by(Message.id)))


def _render_chat(request: Request, db: Session, phone: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "simulador_chat.html", {"messages": _chat(db, phone), "phone": phone}
    )


@router.get("", response_class=HTMLResponse)
def page(request: Request, phone: str = DEFAULT_PHONES[0], name: str = ""):
    phone = _normalize_phone(phone)
    return templates.TemplateResponse(
        request, "simulador.html", {"phone": phone, "name": name, "phones": DEFAULT_PHONES}
    )


@router.get("/chat", response_class=HTMLResponse)
def chat(request: Request, phone: str, db: Session = Depends(get_db)):
    return _render_chat(request, db, _normalize_phone(phone))


@router.post("/send", response_class=HTMLResponse)
def send(
    request: Request,
    phone: str = Form(...),
    name: str = Form(""),
    text: str = Form(""),
    kind: str = Form("text"),
    button_id: str = Form(""),
    db: Session = Depends(get_db),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    phone = _normalize_phone(phone)
    if kind == "text" and not text.strip():
        return _render_chat(request, db, phone)
    msg = IncomingMessage(
        phone=phone,
        type=kind,
        text=text,
        button_id=button_id or None,
        name=name.strip() or None,
    )
    process_incoming(db, msg, client)
    return _render_chat(request, db, phone)


@router.get("/events")
async def events(request: Request, phone: str):
    phone = _normalize_phone(phone)

    async def stream():
        sub = broker.subscribe()
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(sub[1].get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.kind == "sim_message" and event.data.get("phone") == phone:
                    yield "event: refresh\ndata: {}\n\n"
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream")
