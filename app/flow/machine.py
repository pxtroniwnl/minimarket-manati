from app.flow.states import ALLOWED, FlowStep, OrderStatus, PaymentMethod
from app.models import Order
from app.utils import utcnow


class InvalidTransition(Exception):
    pass


def missing_fields(order: Order) -> list[str]:
    missing = []
    if not (order.order_text or "").strip():
        missing.append("order_text")
    if order.torre is None or not order.apto:
        missing.append("direccion")
    if order.payment_method not in {p.value for p in PaymentMethod}:
        missing.append("payment_method")
    elif order.payment_method == PaymentMethod.EFECTIVO and order.cash_amount is None:
        missing.append("cash_amount")
    return missing


def can_transition(order: Order, new: OrderStatus) -> bool:
    return new in ALLOWED[OrderStatus(order.status)]


def transition(order: Order, new: OrderStatus) -> None:
    current = OrderStatus(order.status)
    if new not in ALLOWED[current]:
        raise InvalidTransition(f"{current} → {new} no está permitido")
    if new == OrderStatus.EN_COLA:
        if order.flow_step != FlowStep.RESUMEN:
            raise InvalidTransition("el cliente no ha confirmado el resumen")
        if missing := missing_fields(order):
            raise InvalidTransition(f"faltan datos: {', '.join(missing)}")
        order.queued_at = utcnow()
    if new == OrderStatus.CONFIRMADO and order.total is None:
        raise InvalidTransition("falta el total")
    order.status = new.value
    order.updated_at = utcnow()
