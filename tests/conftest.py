import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("WHATSAPP_MODE", "mock")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ["OPEN_HOUR"] = "0"
os.environ["CLOSE_HOUR"] = "24"
# Los tests no deben depender del .env local.
os.environ["PANEL_PASSWORD"] = "cambiame"
os.environ["SECRET_KEY"] = "secreto-de-pruebas"

import pytest
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
import app.models  # noqa: F401  (registra las tablas)


@pytest.fixture
def engine():
    """SQLite en memoria; con TEST_DATABASE_URL=postgres://… corre contra Postgres real."""
    from sqlalchemy import create_engine, event

    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from app.config import Settings

        eng = create_engine(Settings(database_url=url).database_url)
        Base.metadata.drop_all(eng)
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    if url:
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def SessionTest(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(SessionTest) -> Session:
    with SessionTest() as s:
        yield s


@pytest.fixture
def settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture
def client(SessionTest):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.services.whatsapp import get_whatsapp_client
    from app.services.whatsapp.mock import MockWhatsAppClient

    mock = MockWhatsAppClient()

    def _db():
        with SessionTest() as s:
            yield s

    from app.db import get_session_factory

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory] = lambda: SessionTest
    app.dependency_overrides[get_whatsapp_client] = lambda: mock
    with TestClient(app) as c:
        c.mock = mock
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.services import inbox

    inbox._recent.clear()
    yield


@pytest.fixture
def panel(client):
    """TestClient con sesión del panel iniciada."""
    from app.services import auth

    auth._attempts.clear()
    r = client.post("/login", data={"password": "cambiame"}, follow_redirects=False)
    assert r.status_code == 303
    return client
