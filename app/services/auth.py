"""Login del panel: contraseña única (PANEL_PASSWORD) y cookie firmada."""

import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

COOKIE = "panel_session"
SESSION_DAYS = 30
MAX_ATTEMPTS_PER_MINUTE = 5

_attempts: defaultdict[str, deque] = defaultdict(deque)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="panel")


def make_session() -> str:
    return _serializer().dumps({"ok": 1})


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_DAYS * 86400)
        return True
    except BadSignature:
        return False


def check_password(password: str) -> bool:
    return hmac.compare_digest(password.encode(), get_settings().panel_password.encode())


def too_many_attempts(ip: str) -> bool:
    """5 intentos por minuto por IP [I9]."""
    now = time.monotonic()
    q = _attempts[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS_PER_MINUTE:
        return True
    q.append(now)
    return False


def client_ip(request: Request) -> str:
    # Render y Cloudflare ponen la IP real en X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")


class NotLoggedIn(Exception):
    pass


def require_panel(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE)):
        raise NotLoggedIn
    # Defensa CSRF adicional a SameSite=Lax: los POST deben venir del mismo origen.
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="Origen no permitido")
