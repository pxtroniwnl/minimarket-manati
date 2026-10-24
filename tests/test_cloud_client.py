"""Cliente real de la Cloud API, con transporte simulado (no llama a Meta)."""

import json

import httpx
import pytest

from app.services.whatsapp.base import Button, OutgoingMessage
from app.services.whatsapp.cloud import CloudWhatsAppClient, health


@pytest.fixture(autouse=True)
def _reset_health():
    health.mark_ok()
    yield
    health.mark_ok()


def make(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request, len(calls))

    client = CloudWhatsAppClient("PHONE_ID", "TOKEN", "v26.0", client=httpx.Client(transport=httpx.MockTransport(wrapped)), sleep=lambda s: None)
    return client, calls


def ok(request, n):
    return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": f"wamid.OK{n}"}]})


def error(code, status=400):
    return lambda request, n: httpx.Response(status, json={"error": {"message": f"err {code}", "type": "OAuthException", "code": code}})


def test_text_payload_and_auth_header():
    client, calls = make(ok)
    result = client.send("+573001112233", OutgoingMessage("hola *vecina*"))
    assert result.ok and result.wa_message_id == "wamid.OK1"
    req = calls[0]
    assert str(req.url) == "https://graph.facebook.com/v26.0/PHONE_ID/messages"
    assert req.headers["Authorization"] == "Bearer TOKEN"
    assert json.loads(req.content) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": "573001112233",
        "type": "text", "text": {"body": "hola *vecina*", "preview_url": False},
    }


def test_reply_buttons_payload():
    client, calls = make(ok)
    client.send("+573001112233", OutgoingMessage("¿Cómo pagas?", [Button("pago_nequi:1", "Nequi"), Button("pago_efectivo:1", "Efectivo")]))
    body = json.loads(calls[0].content)
    assert body["type"] == "interactive"
    assert body["interactive"] == {
        "type": "button",
        "body": {"text": "¿Cómo pagas?"},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": "pago_nequi:1", "title": "Nequi"}},
            {"type": "reply", "reply": {"id": "pago_efectivo:1", "title": "Efectivo"}},
        ]},
    }


def test_outside_window_is_not_retried():
    client, calls = make(error(131047))
    result = client.send("+57300", OutgoingMessage("hola"))
    assert not result.ok and result.outside_window
    assert len(calls) == 1


@pytest.mark.parametrize("code,status", [(190, 401), (0, 400)])
def test_H6_invalid_token_flags_health(code, status):
    client, calls = make(error(code, status))
    result = client.send("+57300", OutgoingMessage("hola"))
    assert not result.ok and "Token" in result.error
    assert len(calls) == 1
    assert health.auth_error is not None


def test_H6_health_recovers_after_success():
    health.mark_auth_error("x")
    client, _ = make(ok)
    client.send("+57300", OutgoingMessage("hola"))
    assert health.auth_error is None


@pytest.mark.parametrize("failure", [error(131000, 500), error(130429), error(2, 503)])
def test_H5_transient_errors_are_retried(failure):
    def handler(request, n):
        return failure(request, n) if n < 3 else ok(request, n)

    client, calls = make(handler)
    result = client.send("+57300", OutgoingMessage("hola"))
    assert result.ok and len(calls) == 3


def test_H5_network_error_retried_then_gives_up():
    def handler(request, n):
        raise httpx.ConnectError("sin red")

    client, calls = make(handler)
    result = client.send("+57300", OutgoingMessage("hola"))
    assert not result.ok and result.error == "Sin conexión con WhatsApp"
    assert len(calls) == 3


def test_invalid_parameter_not_retried():
    client, calls = make(error(131009))
    result = client.send("+57300", OutgoingMessage("hola"))
    assert not result.ok and "131009" in result.error
    assert len(calls) == 1


def test_panel_shows_disconnected_banner(panel, db):
    health.mark_auth_error("WhatsApp 190: token vencido")
    assert "WhatsApp desconectado" in panel.get("/panel").text
    health.mark_ok()
    assert "WhatsApp desconectado" not in panel.get("/panel").text
