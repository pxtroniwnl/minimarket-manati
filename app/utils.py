from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    # SQLite devuelve datetimes sin zona; los guardamos siempre en UTC.
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(ZoneInfo(get_settings().tz))


def pesos(n: int | None) -> str:
    if n is None:
        return "—"
    return "$" + f"{n:,}".replace(",", ".")


def mask_phone(phone: str) -> str:
    """Para logs: +573001112233 → +57300***2233."""
    return phone[:6] + "***" + phone[-4:] if len(phone) > 10 else "***"
