"""Limpieza de mensajes viejos para no pasar el almacenamiento gratis de la DB [J2]."""

import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.flow.states import ACTIVE_STATUSES
from app.models import Message, Order
from app.utils import utcnow

log = logging.getLogger(__name__)


def purge_old_messages(db: Session, days: int | None = None) -> int:
    """Borra mensajes de más de `days` días, salvo los de pedidos que siguen activos.

    Los pedidos se conservan (son pocos bytes y alimentan el historial).
    """
    days = days or get_settings().retention_days
    cutoff = utcnow() - timedelta(days=days)
    active_orders = select(Order.id).where(Order.status.in_([s.value for s in ACTIVE_STATUSES]))
    result = db.execute(
        delete(Message).where(
            Message.created_at < cutoff,
            Message.processed_at.is_not(None) | (Message.direction != "in"),
            (Message.order_id.is_(None)) | (Message.order_id.not_in(active_orders)),
        )
    )
    db.commit()
    if result.rowcount:
        log.info("Borrados %s mensajes de más de %s días", result.rowcount, days)
    return result.rowcount or 0
