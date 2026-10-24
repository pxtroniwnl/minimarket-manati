from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.flow.states import HUMAN_STATUSES, QUEUE_STATUSES
from app.models import Order

_queue_values = [s.value for s in QUEUE_STATUSES]


def queue_position(db: Session, order: Order) -> int | None:
    """Puesto en la fila: pedidos activos que llegaron antes + 1. Empates por id [E5]."""
    if order.status not in _queue_values or order.queued_at is None:
        return None
    ahead = db.scalar(
        select(func.count(Order.id)).where(
            Order.status.in_(_queue_values),
            or_(
                Order.queued_at < order.queued_at,
                and_(Order.queued_at == order.queued_at, Order.id < order.id),
            ),
        )
    )
    return (ahead or 0) + 1


def queue_head(db: Session) -> Order | None:
    return db.scalar(
        select(Order)
        .where(Order.status.in_(_queue_values))
        .order_by(Order.queued_at, Order.id)
        .limit(1)
    )


def active_queue(db: Session) -> list[Order]:
    """Pedidos que ve el tendero (incluye en_camino), en orden de llegada."""
    return list(
        db.scalars(
            select(Order)
            .options(joinedload(Order.customer))
            .where(Order.status.in_([s.value for s in HUMAN_STATUSES]))
            .order_by(Order.queued_at, Order.id)
        )
    )


def positions(orders: list[Order]) -> dict[int, int]:
    """Posiciones calculadas en memoria para una lista ya ordenada por queued_at."""
    result, n = {}, 0
    for o in orders:
        if o.status in _queue_values:
            n += 1
            result[o.id] = n
    return result
