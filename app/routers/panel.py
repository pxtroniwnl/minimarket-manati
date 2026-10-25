"""Panel del tendero: cola, detalle del pedido, acciones, historial y tiempo real."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.flow.states import OrderStatus
from app.models import Message, Order
from app.services import auth, orders, store, usage
from app.services.orders import OrderActionError
from app.services.events import broker
from app.services.inbox import _locks
from app.services.outbox import OUTSIDE_WINDOW_ERROR, within_window
from app.services.parsers import parse_amount
from app.services.queue import active_queue, positions
from app.services.whatsapp import get_whatsapp_client
from app.services.whatsapp.base import WhatsAppClient
from app.templating import templates
from app.utils import as_utc, local, pesos, utcnow

router = APIRouter(tags=["panel"])
panel = APIRouter(prefix="/panel", dependencies=[Depends(auth.require_panel)])

STATUS_LABEL = {
    "borrador": "Borrador",
    "en_cola": "En cola",
    "confirmado": "Confirmado",
    "preparando": "Preparando",
    "en_camino": "En camino",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
ACTION_LABEL = {
    OrderStatus.PREPARANDO: "🧑‍🍳 Preparando",
    OrderStatus.EN_CAMINO: "🛵 En camino",
    OrderStatus.ENTREGADO: "✅ Entregado",
}
templates.env.globals["STATUS_LABEL"] = STATUS_LABEL
templates.env.globals["ACTION_LABEL"] = ACTION_LABEL
templates.env.globals["CANCEL_REASONS"] = orders.CANCEL_REASONS


# ------------------------------------------------------------------ login


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form("")):
    if auth.too_many_attempts(auth.client_ip(request)):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Demasiados intentos. Espera un minuto."}, status_code=429
        )
    if not auth.check_password(password):
        return templates.TemplateResponse(request, "login.html", {"error": "Contraseña incorrecta."}, status_code=401)
    resp = RedirectResponse("/panel", status_code=303)
    resp.set_cookie(
        auth.COOKIE,
        auth.make_session(),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---------------------------------------------------------------- helpers


def _wait_info(order: Order, now: datetime) -> dict:
    """Minutos de espera y color de la tarjeta [I7]."""
    since = as_utc(order.queued_at or order.created_at)
    minutes = int((now - since).total_seconds() // 60)
    level = "late" if minutes >= 30 else "warn" if minutes >= 15 else ""
    if order.status in (OrderStatus.PREPARANDO, OrderStatus.EN_CAMINO):
        level = ""
    old = local(since).date() < local(now).date()
    return {"minutes": minutes, "level": level, "old": old}


def _ago(minutes: int) -> str:
    if minutes < 1:
        return "ahora"
    if minutes < 60:
        return f"hace {minutes} min"
    return f"hace {minutes // 60} h {minutes % 60} min"


templates.env.filters["ago"] = _ago


def _get_order(db: Session, order_id: int) -> Order:
    order = db.scalar(select(Order).options(joinedload(Order.customer)).where(Order.id == order_id))
    if order is None:
        raise orders.OrderActionError("No encontré ese pedido.")
    return order


def _help_drafts(db: Session) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .options(joinedload(Order.customer))
            .where(Order.status == OrderStatus.BORRADOR.value, Order.needs_help.is_(True))
            .order_by(Order.updated_at)
        )
    )


def _today_bounds() -> tuple[datetime, datetime]:
    tz = ZoneInfo(get_settings().tz)
    today = datetime.now(tz).date()
    # En UTC: SQLite compara las fechas como texto y las guarda en UTC.
    start = datetime.combine(today, time.min, tzinfo=tz).astimezone(UTC)
    return start, start + timedelta(days=1)


# ------------------------------------------------------------------- cola


@panel.get("", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "panel/index.html", {"paused": store.is_paused(db)})


@panel.get("/cola", response_class=HTMLResponse)
def queue_partial(request: Request, db: Session = Depends(get_db)):
    now = utcnow()
    queue = active_queue(db)
    return templates.TemplateResponse(
        request,
        "panel/_queue.html",
        {
            "queue": queue,
            "positions": positions(queue),
            "wait": {o.id: _wait_info(o, now) for o in queue},
            "help_drafts": _help_drafts(db),
            "pesos": pesos,
        },
    )


@panel.get("/uso", response_class=HTMLResponse)
def usage_partial(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "panel/_usage.html", {"usage": usage.monthly_usage(db)})


@panel.post("/pausa", response_class=HTMLResponse)
def toggle_pause(request: Request, db: Session = Depends(get_db)):
    store.set_paused(db, not store.is_paused(db))
    db.commit()
    return templates.TemplateResponse(request, "panel/_pause.html", {"paused": store.is_paused(db)})


# ------------------------------------------------------------ nuevo pedido

NEW_ORDER_FIELDS = ("name", "phone", "order_text", "address", "payment_method", "cash_amount", "notes", "total")


@panel.get("/nuevo", response_class=HTMLResponse)
def new_order_page(request: Request):
    return templates.TemplateResponse(request, "panel/new.html", {"form": {}, "error": None})


@panel.post("/nuevo", response_class=HTMLResponse)
def create_order(
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    order_text: str = Form(""),
    address: str = Form(""),
    payment_method: str = Form(""),
    cash_amount: str = Form(""),
    notes: str = Form(""),
    total: str = Form(""),
    db: Session = Depends(get_db),
):
    """El tendero carga un pedido que llegó por llamada o en persona [L1]."""
    form = dict(zip(NEW_ORDER_FIELDS, (name, phone, order_text, address, payment_method, cash_amount, notes, total)))
    try:
        order = orders.create_manual(db, **form)
        db.commit()
    except OrderActionError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "panel/_new_form.html", {"form": form, "error": str(exc)})
    except IntegrityError:  # otro celular cargó un pedido para el mismo cliente al mismo tiempo
        db.rollback()
        error = "Ese cliente ya tiene un pedido en curso. Ábrelo desde la fila y edítalo."
        return templates.TemplateResponse(request, "panel/_new_form.html", {"form": form, "error": error})
    broker.publish("new_order", order_id=order.id)
    return Response(status_code=204, headers={"HX-Redirect": f"/panel/pedido/{order.id}"})


# ---------------------------------------------------------------- detalle


def _detail_context(db: Session, order: Order, error: str | None = None, flash: str | None = None) -> dict:
    messages = list(db.scalars(select(Message).where(Message.customer_id == order.customer_id).order_by(Message.id)))
    last_out = next((m for m in reversed(messages) if m.direction == "out"), None)
    return {
        "order": order,
        "messages": messages,
        "position": positions(active_queue(db)).get(order.id),
        "actions": orders.allowed_actions(order),
        "can_cancel": order.status not in (OrderStatus.ENTREGADO, OrderStatus.CANCELADO),
        "editable": order.status in [s.value for s in orders.FIELDS_EDITABLE],
        "no_whatsapp": order.customer.phone is None,
        "outside_window": not within_window(order.customer)
        or bool(last_out and last_out.send_error == OUTSIDE_WINDOW_ERROR),
        "send_failed": bool(last_out and last_out.send_error and last_out.send_error != OUTSIDE_WINDOW_ERROR),
        "last_error": last_out.send_error if last_out else None,
        "error": error,
        "flash": flash,
    }


@panel.get("/pedido/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if order.has_unread:
        order.has_unread = False
        db.commit()
        broker.publish("status_changed", order_id=order.id)
    return templates.TemplateResponse(request, "panel/order.html", _detail_context(db, order))


@panel.get("/pedido/{order_id}/detalle", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if order.has_unread:
        order.has_unread = False
        db.commit()
    return templates.TemplateResponse(request, "panel/_detail.html", _detail_context(db, order))


@panel.get("/pedido/{order_id}/chat", response_class=HTMLResponse)
def order_chat(request: Request, order_id: int, status: str = "", db: Session = Depends(get_db)):
    """Refresca solo el chat (para no borrar lo que el tendero está escribiendo)."""
    order = _get_order(db, order_id)
    if order.has_unread:
        order.has_unread = False
        db.commit()
    resp = templates.TemplateResponse(request, "panel/_chat.html", _detail_context(db, order))
    if status and status != order.status:
        # El estado cambió (p. ej. el cliente canceló): recargar todo el detalle.
        resp.headers["HX-Trigger"] = "reload-detail"
    return resp


def _run_action(request: Request, db: Session, order_id: int, expected: str | None, action, flash: str) -> HTMLResponse:
    """Ejecuta una acción del tendero con el mismo candado que usa el bot para ese cliente."""
    order = _get_order(db, order_id)
    error = None
    with _locks[order.customer.phone]:
        db.refresh(order)
        try:
            orders.check_expected_status(order, expected)
            action(order)
            db.commit()
        except orders.OrderActionError as exc:
            db.rollback()
            error, flash = str(exc), None
            order = _get_order(db, order_id)
    broker.publish("status_changed", order_id=order.id)
    broker.publish("sim_message", phone=order.customer.phone)
    return templates.TemplateResponse(request, "panel/_detail.html", _detail_context(db, order, error, flash))


@panel.post("/pedido/{order_id}/total", response_class=HTMLResponse)
def set_total(
    request: Request,
    order_id: int,
    total: str = Form(""),
    expected: str = Form(""),
    db: Session = Depends(get_db),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    return _run_action(request, db, order_id, expected, lambda o: orders.set_total(db, client, o, total), "Total enviado al cliente ✅")


@panel.post("/total-preview", response_class=HTMLResponse)
def total_preview(request: Request, total: str = Form(""), cash: str = Form("")):
    """Muestra cómo se interpretó el total antes de confirmarlo [I1/I2]."""
    value = parse_amount(total) if total.strip() else None
    cash_value = int(cash) if cash.isdigit() else None
    return templates.TemplateResponse(
        request, "panel/_total_preview.html", {"value": value, "raw": total, "cash": cash_value}
    )


@panel.post("/pedido/{order_id}/estado", response_class=HTMLResponse)
def set_status(
    request: Request,
    order_id: int,
    status: str = Form(...),
    expected: str = Form(""),
    reason: str = Form(""),
    other_reason: str = Form(""),
    db: Session = Depends(get_db),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    try:
        new = OrderStatus(status)
    except ValueError:
        new = None

    def action(order):
        if new is None:
            raise orders.OrderActionError("Estado no válido.")
        why = (other_reason.strip() or None) if reason == "otro" else (reason or None)
        orders.change_status(db, client, order, new, why)

    return _run_action(request, db, order_id, expected, action, f"Listo: {STATUS_LABEL.get(status, status)} ✅")


@panel.post("/pedido/{order_id}/mensaje", response_class=HTMLResponse)
def send_message(
    request: Request,
    order_id: int,
    text: str = Form(""),
    db: Session = Depends(get_db),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    return _run_action(request, db, order_id, None, lambda o: orders.send_message(db, client, o, text), None)


@panel.post("/pedido/{order_id}/editar", response_class=HTMLResponse)
def edit_order(
    request: Request,
    order_id: int,
    expected: str = Form(""),
    order_text: str = Form(""),
    address: str = Form(""),
    payment_method: str = Form(""),
    cash_amount: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    return _run_action(
        request,
        db,
        order_id,
        expected,
        lambda o: orders.edit_order(
            db, o, order_text=order_text, address=address, payment_method=payment_method, cash_amount=cash_amount, notes=notes
        ),
        "Pedido actualizado ✏️",
    )


@panel.post("/pedido/{order_id}/seguir", response_class=HTMLResponse)
def keep_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    return _run_action(request, db, order_id, None, lambda o: orders.keep_order(db, o), "Seguimos con el pedido 👍")


@panel.post("/pedido/{order_id}/devolver-bot", response_class=HTMLResponse)
def return_to_bot(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    client: WhatsAppClient = Depends(get_whatsapp_client),
):
    return _run_action(request, db, order_id, None, lambda o: orders.return_to_bot(db, client, o), "El bot retomó el pedido 🤖")


# -------------------------------------------------------------- historial


@panel.get("/historial", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)):
    start, end = _today_bounds()
    done = list(
        db.scalars(
            select(Order)
            .options(joinedload(Order.customer))
            .where(
                Order.status.in_([OrderStatus.ENTREGADO.value, OrderStatus.CANCELADO.value]),
                Order.queued_at.is_not(None),
                Order.updated_at >= start,
                Order.updated_at < end,
            )
            .order_by(Order.updated_at.desc())
        )
    )
    delivered = [o for o in done if o.status == OrderStatus.ENTREGADO]
    sold = db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.status == OrderStatus.ENTREGADO.value, Order.updated_at >= start, Order.updated_at < end
        )
    )
    return templates.TemplateResponse(
        request,
        "panel/history.html",
        {"orders": done, "delivered": len(delivered), "cancelled": len(done) - len(delivered), "sold": sold},
    )


# ------------------------------------------------------------ tiempo real


@panel.get("/events")
async def events(request: Request):
    """SSE: el navegador recarga la cola/chat y suena con pedidos nuevos [I8]."""

    async def stream():
        loop_queue = broker.subscribe()
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(loop_queue[1].get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.kind in ("new_order", "new_message", "status_changed"):
                    yield event.sse()
        finally:
            broker.unsubscribe(loop_queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


router.include_router(panel)


def not_logged_in_handler(request: Request, exc: auth.NotLoggedIn) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if request.url.path.endswith("/events"):
        return Response(status_code=401)
    return RedirectResponse("/login", status_code=303)


def order_error_handler(request: Request, exc: OrderActionError) -> Response:
    """Pedido inexistente u otra acción imposible fuera de _run_action: 404 legible, nunca 500."""
    return HTMLResponse(f'<div class="banner danger">{exc}</div>', status_code=404)
