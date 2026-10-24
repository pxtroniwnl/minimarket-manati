"""Casos de uso sobre pedidos que comparten el bot y el panel."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings

from app.flow import texts
from app.flow.machine import InvalidTransition, can_transition, transition
from app.flow.states import ACTIVE_STATUSES, FlowStep, OrderStatus, PaymentMethod
from app.models import Customer, Message, Order
from app.services import parsers
from app.services.outbox import send_and_log
from app.services.queue import queue_head
from app.services.whatsapp.base import OutgoingMessage, WhatsAppClient
from app.utils import pesos

CANCEL_REASONS = {
    "sin_producto": "No tenemos lo que pediste 😔",
    "cerrando": "La tienda ya va a cerrar",
    "cliente": "Lo pediste tú",
}

def status_text(status: OrderStatus) -> str | None:
    """Aviso al cliente por cambio de estado. None = no se envía (ahorra mensajes) [K3]."""
    s = get_settings()
    if status == OrderStatus.EN_CAMINO:
        return texts.STATUS_EN_CAMINO
    if status == OrderStatus.PREPARANDO and s.notify_preparing:
        return texts.STATUS_PREPARANDO
    if status == OrderStatus.ENTREGADO and s.notify_delivered:
        return texts.status_entregado()
    return None

# Estados en los que el tendero puede (re)poner el total.
TOTAL_EDITABLE = (OrderStatus.EN_COLA, OrderStatus.CONFIRMADO, OrderStatus.PREPARANDO, OrderStatus.EN_CAMINO)
FIELDS_EDITABLE = TOTAL_EDITABLE


class OrderActionError(Exception):
    """Error mostrable al tendero (no es un 500)."""


def check_expected_status(order: Order, expected: str | None) -> None:
    # Dos celulares con el panel abierto: el segundo toque llega tarde [I6].
    if expected and order.status != expected:
        raise OrderActionError("Este pedido ya cambió de estado. Mira cómo quedó 👇")


def notify_next_in_line(db: Session, client: WhatsAppClient) -> None:
    """Avisa una sola vez al pedido que quedó de primero en la fila [E6]."""
    head = queue_head(db)
    if head is None or head.next_notified:
        return
    if head.status not in (OrderStatus.EN_COLA, OrderStatus.CONFIRMADO):
        return
    head.next_notified = True
    send_and_log(db, client, head.customer, head, OutgoingMessage(texts.NEXT_IN_LINE), sender="bot")


def add_note(db: Session, order: Order, text: str) -> None:
    """Nota interna en el chat del pedido; no se envía al cliente."""
    db.add(Message(order_id=order.id, customer_id=order.customer_id, direction="sys", sender="tendero", text=text))


def parse_total(raw: str) -> int:
    total = parsers.parse_amount(raw)  # [I1] mismas reglas que el efectivo del cliente
    if total is None:
        raise OrderActionError("No entendí el total. Escríbelo así: 12.500 o 12 mil")
    return total


def set_total(db: Session, client: WhatsAppClient, order: Order, raw: str) -> Message:
    if order.status not in TOTAL_EDITABLE:
        raise OrderActionError("Este pedido ya no se puede modificar.")
    order.total = parse_total(raw)
    if order.status == OrderStatus.EN_COLA:
        transition(order, OrderStatus.CONFIRMADO)
        text = texts.total_confirmed(order)
    else:  # [I3] corrección de un total ya enviado
        add_note(db, order, f"Total corregido a {pesos(order.total)}")
        text = texts.total_corrected(order)
    return send_and_log(db, client, order.customer, order, OutgoingMessage(text), sender="tendero")


def change_status(
    db: Session, client: WhatsAppClient, order: Order, new: OrderStatus, reason: str | None = None
) -> Message | None:
    try:
        transition(order, new)
    except InvalidTransition as exc:
        raise OrderActionError(f"No se puede pasar a {new.value.replace('_', ' ')}: {exc}") from exc
    order.cancel_requested = False if new == OrderStatus.CANCELADO else order.cancel_requested
    if new == OrderStatus.CANCELADO:
        reason_text = CANCEL_REASONS.get(reason or "", reason or None)
        add_note(db, order, f"Cancelado por la tienda{f' — {reason_text}' if reason_text else ''}")
        sent = send_and_log(db, client, order.customer, order, OutgoingMessage(texts.status_cancelado(reason_text)), "tendero")
    elif text := status_text(new):
        sent = send_and_log(db, client, order.customer, order, OutgoingMessage(text), "tendero")
    else:
        sent = None
    notify_next_in_line(db, client)
    return sent


def send_message(db: Session, client: WhatsAppClient, order: Order, text: str) -> Message:
    text = text.strip()
    if not text:
        raise OrderActionError("Escribe un mensaje.")
    order.has_unread = False
    return send_and_log(db, client, order.customer, order, OutgoingMessage(text), sender="tendero")


def keep_order(db: Session, order: Order) -> None:
    """El cliente pidió cancelar pero la tienda decide seguir [I10]."""
    order.cancel_requested = False
    add_note(db, order, "Se descartó la solicitud de cancelación")


def _validated_fields(order_text: str, address: str, payment_method: str, cash_amount: str):
    """Valida lo que escribe el tendero en los formularios de pedido. Devuelve (texto, (torre, apto), pago, efectivo)."""
    if not order_text.strip():
        raise OrderActionError("El pedido no puede quedar vacío.")
    result = parsers.parse_address_result(address)
    if not result or not result.ok:
        raise OrderActionError(result.error if result else "No entendí la dirección (ej. T3-502).")
    if payment_method not in {p.value for p in PaymentMethod}:
        raise OrderActionError("Elige una forma de pago.")
    cash = None
    if payment_method == PaymentMethod.EFECTIVO and cash_amount.strip():
        cash = parsers.parse_amount(cash_amount)
        if cash is None:
            raise OrderActionError("No entendí con cuánto paga el cliente.")
    return order_text.strip(), result.pair, payment_method, cash


def edit_order(
    db: Session,
    order: Order,
    *,
    order_text: str,
    address: str,
    payment_method: str,
    cash_amount: str,
    notes: str,
) -> None:
    """El tendero ajusta el pedido (p. ej. el cliente agregó algo por chat) [I4]."""
    if order.status not in FIELDS_EDITABLE:
        raise OrderActionError("Este pedido ya no se puede modificar.")
    text, pair, method, cash = _validated_fields(order_text, address, payment_method, cash_amount)

    changes = []
    if text != (order.order_text or ""):
        changes.append("pedido")
    if pair != (order.torre, order.apto):
        changes.append("dirección")
    if method != order.payment_method or cash != order.cash_amount:
        changes.append("pago")
    if (notes.strip() or None) != order.notes:
        changes.append("notas")
    if not changes:
        return
    order.order_text = text
    order.torre, order.apto = pair
    order.payment_method, order.cash_amount = method, cash
    order.notes = notes.strip() or None
    add_note(db, order, f"✏️ La tienda editó: {', '.join(changes)}")


def create_manual(
    db: Session,
    *,
    name: str,
    phone: str,
    order_text: str,
    address: str,
    payment_method: str,
    cash_amount: str,
    notes: str,
    total: str,
) -> Order:
    """Pedido que llega por llamada o en persona: el tendero lo carga y entra directo a la fila [L1].

    No se le escribe al cliente: iniciar conversación requiere plantillas pagas. Si deja su
    WhatsApp y luego escribe, su chat queda unido a este pedido.
    """
    text, pair, method, cash = _validated_fields(order_text, address, payment_method, cash_amount)
    if method == PaymentMethod.EFECTIVO and cash is None:
        raise OrderActionError("Escribe con cuánto paga el cliente (o el total si paga exacto).")
    amount = parse_total(total) if total.strip() else None

    customer = None
    if phone.strip():
        normalized = parsers.normalize_phone(phone)
        if normalized is None:
            raise OrderActionError("Ese número no parece un celular (ej. 300 111 2233).")
        customer = db.scalar(select(Customer).where(Customer.phone == normalized))
        if customer and db.scalar(
            select(Order.id).where(Order.customer_id == customer.id, Order.status.in_([s.value for s in ACTIVE_STATUSES]))
        ):
            raise OrderActionError("Ese cliente ya tiene un pedido en curso. Ábrelo desde la fila y edítalo.")
        if customer is None:
            customer = Customer(phone=normalized)
            db.add(customer)
        customer.torre, customer.apto = pair  # dirección guardada para su próximo pedido por WhatsApp
    else:
        customer = Customer(phone=None)
        db.add(customer)
    customer.name = name.strip() or customer.name or "Cliente en tienda"

    order = Order(
        customer=customer,
        source="tienda",
        status=OrderStatus.BORRADOR.value,
        order_text=text,
        torre=pair[0],
        apto=pair[1],
        payment_method=method,
        cash_amount=cash,
        notes=notes.strip() or None,
        flow_step=FlowStep.RESUMEN.value,
        # El aviso "eres el siguiente" no aplica: el cliente no está en WhatsApp con nosotros.
        next_notified=True,
    )
    db.add(order)
    transition(order, OrderStatus.EN_COLA)
    if amount is not None:
        order.total = amount
        transition(order, OrderStatus.CONFIRMADO)
    db.flush()
    add_note(db, order, "🏪 Pedido cargado por la tienda")
    return order


def return_to_bot(db: Session, client: WhatsAppClient, order: Order) -> None:
    """Consulta resuelta: el bot retoma el borrador donde iba [G3]."""
    from app.flow.bot import Bot
    from app.services.extractor import build_extractor

    if order.status != OrderStatus.BORRADOR or not order.needs_help:
        raise OrderActionError("Este pedido no está en consulta.")
    order.needs_help = False
    order.has_unread = False
    order.address_attempts = 0
    if order.flow_step == FlowStep.PREGUNTA:
        order.flow_step = FlowStep.PEDIDO.value
    prompt = Bot(build_extractor())._prompt(order.customer, order)
    send_and_log(db, client, order.customer, order, prompt, sender="bot")


def allowed_actions(order: Order) -> list[OrderStatus]:
    """Botones de estado que se le muestran al tendero."""
    nxt = {
        OrderStatus.CONFIRMADO: OrderStatus.PREPARANDO,
        OrderStatus.PREPARANDO: OrderStatus.EN_CAMINO,
        OrderStatus.EN_CAMINO: OrderStatus.ENTREGADO,
    }.get(OrderStatus(order.status))
    return [nxt] if nxt and can_transition(order, nxt) else []
