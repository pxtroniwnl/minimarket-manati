"""Webhook de Meta: verificación, firma, guardado antes de responder, estados. IDs en docs/escenarios.md."""

import hashlib
import hmac
import json

import pytest
from sqlalchemy import func, select

from app.models import Message, Order

SECRET = "app-secret-de-prueba"
PHONE_ID = "106540352242922"


@pytest.fixture(autouse=True)
def meta_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "wa_app_secret", SECRET)
    monkeypatch.setattr(settings, "wa_verify_token", "verifica-esto")
    monkeypatch.setattr(settings, "wa_phone_number_id", PHONE_ID)


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def envelope(messages=None, statuses=None, phone_id=PHONE_ID, name="Ana Pérez", wa_id="573001112233") -> dict:
    value = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "573000000000", "phone_number_id": phone_id}}
    if messages is not None:
        value["contacts"] = [{"profile": {"name": name}, "wa_id": wa_id}]
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"value": value, "field": "messages"}]}]}


_seq = iter(range(10_000))


def text(body, wa_id="573001112233") -> dict:
    return {"from": wa_id, "id": f"wamid.T{next(_seq)}", "timestamp": "1750000000", "type": "text", "text": {"body": body}}


def button(button_id, title="x", wa_id="573001112233") -> dict:
    return {
        "from": wa_id, "id": f"wamid.B{next(_seq)}", "timestamp": "1750000000", "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": title}},
    }


def post(client, payload, signature=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = sign(body)
    return client.post("/webhook", content=body, headers=headers)


# ------------------------------------------------------------ verificación


def test_verify_ok(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verifica-esto", "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444"


@pytest.mark.parametrize("token", ["otro", ""])
def test_verify_wrong_token(client, token):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1"})
    assert r.status_code == 403


def test_verify_without_configured_token(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "wa_verify_token", "")
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
    assert r.status_code == 403


# ------------------------------------------------------------------ firma


def test_missing_signature(client, db):
    assert post(client, envelope([text("hola")]), signature=False).status_code == 403
    assert db.scalar(select(func.count(Message.id))) == 0


def test_bad_signature(client, db):
    body = json.dumps(envelope([text("hola")])).encode()
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert r.status_code == 403
    assert db.scalar(select(func.count(Message.id))) == 0


def test_tampered_body(client):
    body = json.dumps(envelope([text("hola")])).encode()
    sig = sign(body)
    r = client.post("/webhook", content=body.replace(b"hola", b"HOLA"), headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 403


def test_no_app_secret_rejects_everything(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "wa_app_secret", "")
    body = json.dumps(envelope([text("hola")])).encode()
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()})
    assert r.status_code == 403


# ----------------------------------------------------------------- mensajes


def test_text_message_runs_the_bot(client, db):
    r = post(client, envelope([text("hola")]))
    assert r.status_code == 200 and r.json() == {"ok": True}
    to, reply = client.mock.sent[-1]
    assert to == "+573001112233"
    assert "¡Hola, Ana!" in reply.text  # nombre del perfil de WhatsApp
    assert db.scalar(select(Order)).status == "borrador"


def test_full_order_through_webhook(client, db):
    post(client, envelope([text("2 panes pa la t3 502")]))
    order_id = db.scalar(select(Order.id))
    for b in ("pago_nequi", "resumen_ok"):
        post(client, envelope([button(f"{b}:{order_id}")]))
    assert "*#1*" in client.mock.sent[-1][1].text
    db.expire_all()
    assert db.get(Order, order_id).status == "en_cola"


def test_H1_meta_retry_is_deduplicated(client, db):
    payload = envelope([text("hola")])
    post(client, payload)
    post(client, payload)
    assert db.scalar(select(func.count(Message.id)).where(Message.direction == "in")) == 1
    assert len(client.mock.sent) == 1


def test_two_messages_in_one_webhook_are_processed_in_order(client, db):
    post(client, envelope([text("hola"), text("2 panes")]))
    db.expire_all()
    assert db.scalar(select(Order)).order_text == "2 panes"


def test_message_for_other_phone_number_is_ignored(client, db):
    post(client, envelope([text("hola")], phone_id="999"))
    assert db.scalar(select(func.count(Message.id))) == 0


def test_reaction_is_ignored(client, db):
    msg = {"from": "573001112233", "id": "wamid.R1", "timestamp": "1", "type": "reaction", "reaction": {"message_id": "wamid.X", "emoji": "👍"}}
    post(client, envelope([msg]))
    assert client.mock.sent == []
    assert db.scalar(select(func.count(Order.id))) == 0


def test_edited_message_in_draft(client, db):
    post(client, envelope([text("hola")]))
    edit = {"from": "573001112233", "id": "wamid.E1", "timestamp": "1", "type": "unsupported",
            "errors": [{"code": 131051, "title": "Message type unknown"}], "unsupported": {"type": "edit"}}
    post(client, envelope([edit]))
    assert "editaste un mensaje" in client.mock.sent[-1][1].text


def test_audio_in_draft(client):
    post(client, envelope([text("hola")]))
    audio = {"from": "573001112233", "id": "wamid.A1", "timestamp": "1", "type": "audio", "audio": {"id": "123", "mime_type": "audio/ogg"}}
    post(client, envelope([audio]))
    assert "solo puedo leer mensajes de texto" in client.mock.sent[-1][1].text


def test_non_message_payload(client):
    r = post(client, {"object": "page", "entry": []})
    assert r.status_code == 200


def test_bad_json(client):
    body = b"{no es json"
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 400


# ---------------------------------------------------------- robustez [H4]


def test_H4_database_down_returns_500(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.routers import webhook

    def down(*a, **kw):
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    monkeypatch.setattr(webhook, "store_incoming", down)
    r = post(client, envelope([text("hola")]))
    assert r.status_code == 500  # Meta reintentará


def test_H3_bot_crash_in_background_keeps_message_pending(client, db, monkeypatch):
    from app.routers import webhook

    def boom(*a, **kw):
        raise RuntimeError("se cayó")

    monkeypatch.setattr(webhook, "process_pending", boom)
    assert post(client, envelope([text("hola")])).status_code == 200
    db.expire_all()
    inbound = db.scalar(select(Message).where(Message.direction == "in"))
    assert inbound.processed_at is None  # quedará para el reproceso al arrancar
    monkeypatch.undo()

    from app.services.inbox import reprocess_pending

    assert reprocess_pending(db, client.mock) == 1
    assert "¿Qué quieres pedir?" in client.mock.sent[-1][1].text


# ------------------------------------------------------------------ estados


def test_failed_status_marks_message(client, db):
    post(client, envelope([text("hola")]))
    out = db.scalar(select(Message).where(Message.direction == "out"))
    failed = {"id": out.wa_message_id, "status": "failed", "timestamp": "1", "recipient_id": "573001112233",
              "errors": [{"code": 131049, "title": "This message was not delivered to maintain healthy ecosystem engagement."}]}
    post(client, envelope(statuses=[failed]))
    db.expire_all()
    assert db.get(Message, out.id).send_error.startswith("No entregado (131049)")


def test_failed_status_outside_window(client, db):
    post(client, envelope([text("hola")]))
    out = db.scalar(select(Message).where(Message.direction == "out"))
    failed = {"id": out.wa_message_id, "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement message"}]}
    post(client, envelope(statuses=[failed]))
    db.expire_all()
    assert "debe escribir primero" in db.get(Message, out.id).send_error


def test_delivered_status_is_ignored(client, db):
    post(client, envelope([text("hola")]))
    out = db.scalar(select(Message).where(Message.direction == "out"))
    post(client, envelope(statuses=[{"id": out.wa_message_id, "status": "delivered"}]))
    db.expire_all()
    assert db.get(Message, out.id).send_error is None
