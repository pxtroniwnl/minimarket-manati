"""Panel del tendero. Los IDs (I1…, F5, G3, H7) remiten a docs/escenarios.md."""

from datetime import timedelta

from sqlalchemy import select

from app.flow.states import OrderStatus
from app.models import Message, Order
from app.utils import utcnow
from tests.factories import Resident


def queued_order(db, phone="+573001112233", pay="pago_nequi", cash="50", name=None) -> Order:
    r = Resident(db, phone=phone, name=name)
    r.says("hola")
    r.says("2 panes y una leche")
    r.says("t3 502")
    r.taps(pay)
    if pay == "pago_efectivo":
        r.says(cash)
    r.taps("resumen_ok")
    return r.order


def reload(db, order) -> Order:
    db.expire_all()
    return db.get(Order, order.id)


def last_sent(panel):
    return panel.mock.sent[-1][1].text


# ------------------------------------------------------------------ login


def test_panel_requires_login(client):
    r = client.get("/panel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = client.get("/panel/cola", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"


def test_wrong_password(client):
    from app.services import auth

    auth._attempts.clear()
    r = client.post("/login", data={"password": "nop"})
    assert r.status_code == 401 and "Contraseña incorrecta" in r.text


def test_I9_login_rate_limit(client):
    from app.services import auth

    auth._attempts.clear()
    codes = [client.post("/login", data={"password": "nop"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # Ni con la contraseña correcta entra mientras esté bloqueado.
    assert client.post("/login", data={"password": "cambiame"}, follow_redirects=False).status_code == 429


def test_I9_session_cookie_flags(client):
    from app.services import auth

    auth._attempts.clear()
    r = client.post("/login", data={"password": "cambiame"}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_tampered_cookie_is_rejected(client):
    client.cookies.set("panel_session", "falsa")
    assert client.get("/panel", follow_redirects=False).status_code == 303


def test_cross_origin_post_is_rejected(panel, db):
    o = queued_order(db)
    r = panel.post(f"/panel/pedido/{o.id}/mensaje", data={"text": "hola"}, headers={"Origin": "https://malo.com"})
    assert r.status_code == 403


# ------------------------------------------------------------------- cola


def test_queue_shows_orders_in_order(panel, db):
    a = queued_order(db, phone="+573000000001")
    b = queued_order(db, phone="+573000000002")
    html = panel.get("/panel/cola").text
    assert html.index(f"/panel/pedido/{a.id}") < html.index(f"/panel/pedido/{b.id}")
    assert "#1" in html and "#2" in html and "T3-502" in html


def test_I7_waiting_colors_and_old_orders(panel, db):
    o = queued_order(db)
    o.queued_at = utcnow() - timedelta(minutes=20)
    db.commit()
    assert 'class="card warn' in panel.get("/panel/cola").text
    o.queued_at = utcnow() - timedelta(days=1)
    db.commit()
    html = panel.get("/panel/cola").text
    assert 'class="card late' in html and "de ayer" in html


def test_unread_dot_and_opening_marks_read(panel, db):
    o = queued_order(db)
    Resident(db).says("¿me agregas un hielo?")
    assert "Mensajes sin leer" in panel.get("/panel/cola").text
    html = panel.get(f"/panel/pedido/{o.id}").text
    assert "¿me agregas un hielo?" in html
    assert reload(db, o).has_unread is False


def test_J3_customer_text_is_escaped(panel, db):
    r = Resident(db)
    r.says("hola")
    r.says("<script>alert(1)</script>")
    r.says("t3 502")
    r.taps("pago_nequi")
    r.taps("resumen_ok")
    html = panel.get("/panel/cola").text + panel.get(f"/panel/pedido/{r.order.id}").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ------------------------------------------------------------------ total


def test_confirm_total_notifies_with_change(panel, db):
    o = queued_order(db, pay="pago_efectivo", cash="50")
    r = panel.post(f"/panel/pedido/{o.id}/total", data={"total": "12.500", "expected": "en_cola"})
    assert "Total enviado al cliente" in r.text
    o = reload(db, o)
    assert (o.status, o.total) == (OrderStatus.CONFIRMADO, 12_500)
    msg = last_sent(panel)
    assert "$12.500" in msg and "te llevamos $37.500 de cambio" in msg


def test_I1_total_formats(panel, db):
    for i, (raw, expected) in enumerate([("12500", 12_500), ("12 mil", 12_000), ("$8.900", 8_900)]):
        o = queued_order(db, phone=f"+57300000010{i}")
        panel.post(f"/panel/pedido/{o.id}/total", data={"total": raw, "expected": "en_cola"})
        assert reload(db, o).total == expected


def test_I1_invalid_total(panel, db):
    o = queued_order(db)
    r = panel.post(f"/panel/pedido/{o.id}/total", data={"total": "doce", "expected": "en_cola"})
    assert "No entendí el total" in r.text
    assert reload(db, o).status == OrderStatus.EN_COLA


def test_I1_I2_total_preview(panel):
    r = panel.post("/panel/total-preview", data={"total": "12 mil", "cash": "10000"})
    assert "$12.000" in r.text and "le faltan $2.000" in r.text
    r = panel.post("/panel/total-preview", data={"total": "xx", "cash": ""})
    assert "No entiendo" in r.text


def test_I2_cash_not_enough_tells_customer(panel, db):
    o = queued_order(db, pay="pago_efectivo", cash="10")
    r = panel.post(f"/panel/pedido/{o.id}/total", data={"total": "12.000", "expected": "en_cola"})
    assert "le faltan $2.000" in r.text
    assert "te faltarían $2.000" in last_sent(panel)


def test_I3_total_correction(panel, db):
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/total", data={"total": "12000", "expected": "en_cola"})
    r = panel.post(f"/panel/pedido/{o.id}/total", data={"total": "13000", "expected": "confirmado"})
    assert reload(db, o).total == 13_000
    assert reload(db, o).status == OrderStatus.CONFIRMADO
    assert last_sent(panel).startswith("Corrección: el total de tu pedido es *$13.000*")
    assert "Total corregido a $13.000" in r.text


# ---------------------------------------------------------------- estados


def test_full_status_flow_sends_messages(panel, db, settings, monkeypatch):
    monkeypatch.setattr(settings, "notify_preparing", True)
    monkeypatch.setattr(settings, "notify_delivered", True)
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/total", data={"total": "5000", "expected": "en_cola"})
    expected_texts = {
        "preparando": "preparando tu pedido",
        "en_camino": "va en camino",
        "entregado": "Pedido entregado",
    }
    prev = "confirmado"
    for status, fragment in expected_texts.items():
        panel.post(f"/panel/pedido/{o.id}/estado", data={"status": status, "expected": prev})
        assert fragment in last_sent(panel)
        prev = status
    assert reload(db, o).status == OrderStatus.ENTREGADO


def test_cannot_skip_states(panel, db):
    o = queued_order(db)
    r = panel.post(f"/panel/pedido/{o.id}/estado", data={"status": "en_camino", "expected": "en_cola"})
    assert r.status_code == 200 and "No se puede pasar a en camino" in r.text
    assert reload(db, o).status == OrderStatus.EN_COLA


def test_I6_two_devices_same_button(panel, db):
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/total", data={"total": "5000", "expected": "en_cola"})
    sent = len(panel.mock.sent)
    # El segundo celular todavía ve "en_cola" y vuelve a confirmar total.
    r = panel.post(f"/panel/pedido/{o.id}/total", data={"total": "5000", "expected": "en_cola"})
    assert r.status_code == 200 and "ya cambió de estado" in r.text
    assert len(panel.mock.sent) == sent  # no se le escribe dos veces al cliente


def test_I5_cancel_with_reason(panel, db):
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/estado", data={"status": "cancelado", "expected": "en_cola", "reason": "sin_producto"})
    assert reload(db, o).status == OrderStatus.CANCELADO
    assert "Motivo: No tenemos lo que pediste" in last_sent(panel)


def test_I5_cancel_with_custom_reason(panel, db):
    o = queued_order(db)
    panel.post(
        f"/panel/pedido/{o.id}/estado",
        data={"status": "cancelado", "expected": "en_cola", "reason": "otro", "other_reason": "Se fue la luz"},
    )
    assert "Motivo: Se fue la luz" in last_sent(panel)


def test_E6_cancel_from_panel_notifies_next(panel, db):
    a = queued_order(db, phone="+573000000001")
    b = queued_order(db, phone="+573000000002")
    panel.post(f"/panel/pedido/{a.id}/estado", data={"status": "cancelado", "expected": "en_cola"})
    to_b = [m.text for phone, m in panel.mock.sent if phone == "+573000000002"]
    assert any("Eres el siguiente" in t for t in to_b)
    assert reload(db, b).next_notified


def test_I10_cancel_requested_keep(panel, db):
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/total", data={"total": "5000", "expected": "en_cola"})
    Resident(db).says("cancelar")
    assert "El cliente pidió cancelar" in panel.get("/panel/cola").text
    r = panel.post(f"/panel/pedido/{o.id}/seguir")
    o = reload(db, o)
    assert o.cancel_requested is False and o.status == OrderStatus.CONFIRMADO
    assert "Se descartó la solicitud de cancelación" in r.text


def test_I10_cancel_requested_accept(panel, db):
    o = queued_order(db)
    panel.post(f"/panel/pedido/{o.id}/total", data={"total": "5000", "expected": "en_cola"})
    Resident(db).says("cancelar")
    panel.post(f"/panel/pedido/{o.id}/estado", data={"status": "cancelado", "expected": "confirmado", "reason": "cliente"})
    o = reload(db, o)
    assert o.status == OrderStatus.CANCELADO and o.cancel_requested is False


# ----------------------------------------------------------- edición y chat


def test_I4_edit_order(panel, db):
    o = queued_order(db)
    r = panel.post(
        f"/panel/pedido/{o.id}/editar",
        data={
            "expected": "en_cola",
            "order_text": "2 panes, una leche y un hielo",
            "address": "t4 1101",
            "payment_method": "efectivo",
            "cash_amount": "20 mil",
            "notes": "",
        },
    )
    o = reload(db, o)
    assert (o.order_text, o.torre, o.apto, o.payment_method, o.cash_amount) == (
        "2 panes, una leche y un hielo", 4, "1101", "efectivo", 20_000,
    )
    assert "La tienda editó: pedido, dirección, pago" in r.text
    note = db.scalar(select(Message).where(Message.direction == "sys"))
    assert note is not None  # nota interna, no enviada
    assert not any("editó" in m.text for _, m in panel.mock.sent)


def test_I4_edit_rejects_invalid_address(panel, db):
    o = queued_order(db)
    r = panel.post(
        f"/panel/pedido/{o.id}/editar",
        data={"expected": "en_cola", "order_text": "x", "address": "t2 905", "payment_method": "nequi", "cash_amount": "", "notes": ""},
    )
    assert "La torre 2 tiene 7 pisos" in r.text
    assert reload(db, o).torre == 3


def test_tendero_message(panel, db):
    o = queued_order(db)
    r = panel.post(f"/panel/pedido/{o.id}/mensaje", data={"text": "No hay leche entera, ¿deslactosada?"})
    assert panel.mock.sent[-1] == ("+573001112233", panel.mock.sent[-1][1])
    assert "deslactosada" in last_sent(panel)
    m = db.scalar(select(Message).where(Message.sender == "tendero"))
    assert m.direction == "out" and m.order_id == o.id
    assert "deslactosada" in r.text


def test_H7_outside_24h_window(panel, db):
    o = queued_order(db)
    o.customer.last_inbound_at = utcnow() - timedelta(hours=25)
    db.commit()
    sent = len(panel.mock.sent)
    r = panel.post(f"/panel/pedido/{o.id}/mensaje", data={"text": "hola?"})
    assert "El cliente debe escribir primero" in r.text
    assert len(panel.mock.sent) == sent  # no se intentó enviar


def test_chat_refresh_signals_status_change(panel, db):
    o = queued_order(db)
    r = panel.get(f"/panel/pedido/{o.id}/chat", params={"status": "en_cola"})
    assert "HX-Trigger" not in r.headers
    Resident(db).says("cancelar")
    r = panel.get(f"/panel/pedido/{o.id}/chat", params={"status": "en_cola"})
    assert r.headers.get("HX-Trigger") == "reload-detail"


# --------------------------------------------------------------- consultas


def test_G2_G3_help_draft_and_return_to_bot(panel, db):
    r = Resident(db)
    r.says("hola")
    r.says("2 panes")
    r.says("hablar con la tienda")
    html = panel.get("/panel/cola").text
    assert "Consultas" in html and f"/panel/pedido/{r.order.id}" in html
    panel.post(f"/panel/pedido/{r.order.id}/mensaje", data={"text": "Sí tenemos, dale"})
    resp = panel.post(f"/panel/pedido/{r.order.id}/devolver-bot")
    assert "El bot retomó el pedido" in resp.text
    o = reload(db, r.order)
    assert o.needs_help is False
    # El bot repite la pregunta del paso en el que iba (dirección).
    assert "torre y apartamento" in last_sent(panel)
    r.says("t3 502")
    assert reload(db, o).flow_step == "pago"


# ---------------------------------------------------------- pausa e historial


def test_F5_pause_blocks_new_orders(panel, db):
    panel.post("/panel/pausa")
    replies = Resident(db).says("hola")
    assert "no estamos recibiendo pedidos" in replies[0].text
    assert db.scalar(select(Order)) is None
    r = panel.post("/panel/pausa")
    assert "Pausar pedidos" in r.text
    assert "¿Qué quieres pedir?" in Resident(db).says("hola")[0].text


def test_history_today(panel, db):
    a = queued_order(db, phone="+573000000001")
    b = queued_order(db, phone="+573000000002")
    for o, total in ((a, "12000"), (b, "8000")):
        panel.post(f"/panel/pedido/{o.id}/total", data={"total": total, "expected": "en_cola"})
    for st, prev in (("preparando", "confirmado"), ("en_camino", "preparando"), ("entregado", "en_camino")):
        panel.post(f"/panel/pedido/{a.id}/estado", data={"status": st, "expected": prev})
    panel.post(f"/panel/pedido/{b.id}/estado", data={"status": "cancelado", "expected": "confirmado"})
    html = panel.get("/panel/historial").text
    assert "$12.000" in html  # solo cuenta lo entregado
    assert html.count('class="card"') == 2


def test_pages_render(panel, db):
    o = queued_order(db)
    for url in ("/panel", "/panel/cola", f"/panel/pedido/{o.id}", "/panel/historial"):
        assert panel.get(url).status_code == 200, url


def test_unknown_order(panel):
    for r in (panel.get("/panel/pedido/9999"), panel.post("/panel/pedido/9999/mensaje", data={"text": "x"})):
        assert r.status_code == 404 and "No encontré ese pedido" in r.text


def test_K4_usage_strip_in_panel(panel, db, settings, monkeypatch):
    queued_order(db)
    html = panel.get("/panel/uso").text
    assert "mensajes gratis este mes" in html and "/ 1.000" in html
    monkeypatch.setattr(settings, "wa_free_tier", 2)
    assert "Meta ya está cobrando" in panel.get("/panel/uso").text
