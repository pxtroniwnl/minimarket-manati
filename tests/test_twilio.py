"""Conector Twilio (Sandbox de WhatsApp) [M1–M5]. No llama a Twilio: transporte simulado."""

import itertools
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.models import Message, Order
from app.services.whatsapp.base import Button, OutgoingMessage
from app.services.whatsapp.cloud import health
from app.services.whatsapp.twilio import TwilioWhatsAppClient, parse_form, render_text, signature

TOKEN = "auth-token-de-prueba"
URL = "http://testserver/twilio/webhook"
PHONE = "+573001112233"
_sid = itertools.count()


@pytest.fixture(autouse=True)
def twilio_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", TOKEN)
    monkeypatch.setattr(settings, "public_url", "")
    health.mark_ok()
    yield
    health.mark_ok()


def form(body="", **extra) -> dict:
    return {
        "AccountSid": "AC123", "From": f"whatsapp:{PHONE}", "To": "whatsapp:+14155238886",
        "Body": body, "MessageSid": f"SM{next(_sid)}", "NumMedia": "0", "ProfileName": "Ana Pérez", **extra,
    }


def post(client, data, url=URL, sign=True):
    headers = {"X-Twilio-Signature": signature(url, data, TOKEN)} if sign else {}
    return client.post(url.removeprefix("http://testserver"), data=data, headers=headers)


def says(client, body):
    r = post(client, form(body))
    assert r.status_code == 200 and "<Response>" in r.text
    return client.mock.sent[-1][1]


# ------------------------------------------------------------------ firma


def test_M1_signature_required(client, db):
    assert post(client, form("hola"), sign=False).status_code == 403
    bad = client.post("/twilio/webhook", data=form("hola"), headers={"X-Twilio-Signature": "AAAA"})
    assert bad.status_code == 403
    assert db.scalars(select(Message)).all() == []


def test_M1_no_token_configured_rejects_everything(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    data = form("hola")
    r = client.post("/twilio/webhook", data=data, headers={"X-Twilio-Signature": signature(URL, data, "")})
    assert r.status_code == 403


def test_M1_signature_uses_public_url(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://minimarket-manati.onrender.com")
    data = form("hola")
    public = "https://minimarket-manati.onrender.com/twilio/webhook"
    r = client.post("/twilio/webhook", data=data, headers={"X-Twilio-Signature": signature(public, data, TOKEN)})
    assert r.status_code == 200


# --------------------------------------------------------------- entrantes


def test_M2_parse_form_types():
    assert parse_form(form("hola")).phone == PHONE
    msg = parse_form(form("hola"))
    assert (msg.type, msg.text, msg.name) == ("text", "hola", "Ana Pérez")
    assert parse_form(form(ButtonPayload="pago_nequi:3", ButtonText="Nequi")).button_id == "pago_nequi:3"
    assert parse_form(form(NumMedia="1", MediaContentType0="image/jpeg")).type == "image"
    assert parse_form(form(NumMedia="1", MediaContentType0="audio/ogg")).type == "audio"
    assert parse_form(form(NumMedia="1", MediaContentType0="application/pdf")).type == "document"
    assert parse_form(form(Latitude="10.4", Longitude="-75.5")).type == "location"


def test_M3_full_order_with_numbered_replies(client, db):
    reply = says(client, "hola")
    assert "Hola" in reply.text
    says(client, "2 panes y una leche")
    pay = says(client, "t3 502")
    assert [b.id.split(":")[0] for b in pay.buttons] == ["pago_efectivo", "pago_nequi", "pago_transf"]
    summary = says(client, "2")  # = tocar "Nequi"
    assert "resumen_ok" in summary.buttons[0].id
    done = says(client, "1")  # = tocar "Confirmar"
    assert "#1" in done.text
    order = db.scalars(select(Order)).one()
    assert (order.status, order.payment_method) == ("en_cola", "nequi")


def test_M3_number_without_options_stays_text(client, db):
    says(client, "hola")
    reply = says(client, "2")  # el saludo no tiene opciones: "2" es texto normal
    assert reply.buttons == [] or all(not b.id.startswith("resumen_ok") for b in reply.buttons)
    inbound = db.scalars(select(Message).where(Message.direction == "in").order_by(Message.id)).all()[-1]
    assert inbound.payload_json["type"] == "text"


def test_M3_duplicate_message_sid_ignored(client, db):
    data = form("hola")
    post(client, data)
    post(client, data)
    assert len(db.scalars(select(Message).where(Message.direction == "in")).all()) == 1


def test_M4_status_callback_marks_failure(client, db):
    says(client, "hola")
    out = db.scalars(select(Message).where(Message.direction == "out")).all()[-1]
    out.wa_message_id = "SMfail1"
    db.commit()
    data = {"MessageSid": "SMfail1", "MessageStatus": "undelivered", "ErrorCode": "63016"}
    assert post(client, data, url="http://testserver/twilio/status").status_code == 200
    db.expire_all()
    assert "fuera de las 24 h" in db.get(Message, out.id).send_error


# ----------------------------------------------------------------- cliente


def make(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request, len(calls))

    transport = httpx.Client(transport=httpx.MockTransport(wrapped))
    return TwilioWhatsAppClient("AC123", "tok", "whatsapp:+14155238886", "https://x/twilio/status", client=transport, sleep=lambda s: None), calls


def ok(request, n):
    return httpx.Response(201, json={"sid": f"SM{n}", "status": "queued"})


def error(code, status=400):
    return lambda request, n: httpx.Response(status, json={"code": code, "message": f"err {code}", "status": status})


def test_M5_client_payload_and_numbered_buttons():
    client, calls = make(ok)
    msg = OutgoingMessage("¿Cómo pagas?", [Button("pago_efectivo:1", "💵 Efectivo"), Button("pago_nequi:1", "📱 Nequi")])
    result = client.send(PHONE, msg)
    assert result.ok and result.wa_message_id == "SM1"
    sent = dict(httpx.QueryParams(calls[0].content.decode()))
    assert sent["To"] == f"whatsapp:{PHONE}" and sent["From"] == "whatsapp:+14155238886"
    assert sent["StatusCallback"] == "https://x/twilio/status"
    assert "1️⃣ 💵 Efectivo" in sent["Body"] and "2️⃣ 📱 Nequi" in sent["Body"]
    assert calls[0].headers["authorization"].startswith("Basic ")
    assert render_text(OutgoingMessage("solo texto")) == "solo texto"


def test_M5_long_text_is_split_to_1600():
    client, calls = make(ok)
    client.send(PHONE, OutgoingMessage("palabra " * 500))
    bodies = [dict(httpx.QueryParams(c.content.decode()))["Body"] for c in calls]
    assert len(bodies) == 3 and all(len(b) <= 1600 for b in bodies)


def test_M5_errors():
    client, _ = make(error(63016))
    assert client.send(PHONE, OutgoingMessage("x")).outside_window
    client, _ = make(error(63015))
    assert "Sandbox" in client.send(PHONE, OutgoingMessage("x")).error
    client, _ = make(error(20003, 401))
    assert not client.send(PHONE, OutgoingMessage("x")).ok and health.auth_error
    client, calls = make(lambda r, n: httpx.Response(503) if n < 3 else ok(r, n))
    assert client.send(PHONE, OutgoingMessage("x")).ok and len(calls) == 3


def test_M5_startup_requires_twilio_credentials(settings):
    from app.startup import ConfigError, validate_settings

    s = settings.model_copy(update={"whatsapp_mode": "twilio", "twilio_account_sid": "", "twilio_auth_token": ""})
    with pytest.raises(ConfigError, match="TWILIO_ACCOUNT_SID"):
        validate_settings(s)


def test_month_start_is_utc_even_late_in_the_month():
    from app.services.usage import month_start

    late = datetime(2026, 10, 31, 21, 0, tzinfo=ZoneInfo("America/Bogota"))  # ya es 1 de noviembre en UTC
    assert month_start(late) == datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
