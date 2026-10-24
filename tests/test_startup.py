import logging

import pytest

from app.config import Settings
from app.startup import ConfigError, RedactSecrets, validate_settings

GOOD = dict(
    whatsapp_mode="cloud",
    wa_phone_number_id="1",
    wa_access_token="t",
    wa_verify_token="v",
    wa_app_secret="s",
    panel_password="clave-segura",
    secret_key="x" * 32,
    database_url="postgresql+psycopg://u:p@host/db",
)


def test_cloud_with_everything_is_ok():
    validate_settings(Settings(**GOOD))


def test_mock_mode_never_blocks():
    validate_settings(Settings(whatsapp_mode="mock", panel_password="cambiame"))


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"wa_app_secret": ""}, "WA_APP_SECRET"),
        ({"wa_access_token": ""}, "WA_ACCESS_TOKEN"),
        ({"panel_password": "cambiame"}, "PANEL_PASSWORD"),
        ({"secret_key": "dev-secret-cambiame"}, "SECRET_KEY"),
        ({"secret_key": "corta"}, "SECRET_KEY"),
        ({"database_url": "sqlite:///./dev.db"}, "DATABASE_URL"),
    ],
)
def test_cloud_rejects_unsafe_config(override, fragment):
    with pytest.raises(ConfigError, match=fragment):
        validate_settings(Settings(**{**GOOD, **override}))


def test_J4_verify_token_redacted_from_access_log():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/webhook?hub.mode=subscribe&hub.verify_token=SUPERSECRETO&hub.challenge=1", "1.1", 200), None,
    )
    RedactSecrets().filter(record)
    assert "SUPERSECRETO" not in record.getMessage()
    assert "hub.verify_token=***" in record.getMessage()


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@ep-x.neon.tech/db?sslmode=require", "postgresql://u:p@ep-x.neon.tech/db?sslmode=require"],
)
def test_postgres_urls_use_psycopg3(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
