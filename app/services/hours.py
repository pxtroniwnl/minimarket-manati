from datetime import datetime

from app.config import get_settings
from app.utils import local, utcnow


def is_open(now: datetime | None = None) -> bool:
    s = get_settings()
    hour = local(now or utcnow()).hour
    if s.open_hour <= s.close_hour:
        return s.open_hour <= hour < s.close_hour
    # Horario que cruza la medianoche (p. ej. 18 → 2).
    return hour >= s.open_hour or hour < s.close_hour
