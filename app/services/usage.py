"""Cuántos mensajes de servicio se han enviado este mes (Meta regala 1.000 por número al mes)."""

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Message


@dataclass
class Usage:
    sent: int
    free_tier: int

    @property
    def ratio(self) -> float:
        return self.sent / self.free_tier if self.free_tier else 0

    @property
    def level(self) -> str:
        return "over" if self.ratio >= 1 else "warn" if self.ratio >= 0.8 else "ok"


def month_start(now: datetime | None = None) -> datetime:
    tz = ZoneInfo(get_settings().tz)
    now = (now or datetime.now(tz)).astimezone(tz)
    # En UTC: SQLite compara las fechas como texto y las guarda en UTC.
    return datetime.combine(now.date().replace(day=1), time.min, tzinfo=tz).astimezone(UTC)


def monthly_usage(db: Session, now: datetime | None = None) -> Usage:
    """Cuenta los salientes aceptados por Meta este mes (cota superior de los 'entregados') [K4]."""
    sent = db.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "out",
            Message.wa_message_id.is_not(None),
            Message.send_error.is_(None),
            Message.created_at >= month_start(now),
        )
    )
    return Usage(sent=sent or 0, free_tier=get_settings().wa_free_tier)
