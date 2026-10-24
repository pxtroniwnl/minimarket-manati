"""Validación de configuración y tareas de arranque."""

import logging
import re

from app.config import Settings

log = logging.getLogger(__name__)

DEFAULT_PASSWORD = "cambiame"
DEFAULT_SECRET = "dev-secret-cambiame"


class ConfigError(RuntimeError):
    pass


def validate_settings(s: Settings) -> None:
    """En producción (cloud) no se arranca con secretos por defecto ni datos de Meta faltantes."""
    if s.whatsapp_mode != "cloud":
        return
    missing = [
        name
        for name, value in (
            ("WA_PHONE_NUMBER_ID", s.wa_phone_number_id),
            ("WA_ACCESS_TOKEN", s.wa_access_token),
            ("WA_VERIFY_TOKEN", s.wa_verify_token),
            ("WA_APP_SECRET", s.wa_app_secret),
        )
        if not value
    ]
    if s.panel_password == DEFAULT_PASSWORD:
        missing.append("PANEL_PASSWORD (sigue en el valor por defecto)")
    if s.secret_key == DEFAULT_SECRET or len(s.secret_key) < 16:
        missing.append("SECRET_KEY (por defecto o muy corta)")
    if s.database_url.startswith("sqlite"):
        missing.append("DATABASE_URL (SQLite se borra en el hosting gratis: usa Postgres)")
    if missing:
        raise ConfigError("Configuración incompleta para WHATSAPP_MODE=cloud: " + "; ".join(missing))


class RedactSecrets(logging.Filter):
    """Evita que el verify token de Meta quede en el log de accesos [J4]."""

    _re = re.compile(r"(hub\.verify_token=)[^&\s]*")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._re.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True
