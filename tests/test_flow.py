from datetime import UTC, datetime

from sqlalchemy import func, select

from app.flow.states import FlowStep, OrderStatus
from app.models import Customer, Message, Order
from tests.factories import PAYMENT_BUTTONS, SUMMARY_BUTTONS, Resident, button_ids


def order_until_summary(r: Resident, pay="pago_nequi"):
    """Llega hasta el resumen, que sale justo después del pago (sin paso de notas)."""
    r.says("hola")
    r.says("2 panes y una leche")
    r.says("torre 3 apto 502")
    replies = r.taps(pay)
    if pay == "pago_efectivo":
        replies = r.says("50")
    return replies


def test_greeting_creates_draft_without_queue_spot(db):
    r = Resident(db, name="Ana María")
    replies = r.says("hola")
    assert "¡Hola, Ana! 👋" in replies[0].text
    assert "¿Qué quieres pedir?" in replies[0].text
    assert r.order.status == OrderStatus.BORRADOR
    assert r.order.queued_at is None


def test_full_flow_new_customer_cash(db):
    r = Resident(db)
    r.says("hola")
    replies = r.says("2 panes, 1 leche entera y 1 coca cola 1.5")
    assert "torre y apartamento" in replies[0].text
    assert r.order.order_text == "2 panes, 1 leche entera y 1 coca cola 1.5"

    replies = r.says("t3 502")
    assert button_ids(replies) == ["pago_efectivo", "pago_nequi", "pago_transf"]

    replies = r.taps("pago_efectivo")
    assert "¿Con cuánto pagas?" in replies[0].text

    replies = r.says("veinte mil")
    assert "en números" in replies[0].text
    assert r.order.cash_amount is None

    replies = r.says("50")
    assert r.order.cash_amount == 50_000
    # Sin paso de notas obligatorio: va directo al resumen [K2].
    assert button_ids(replies) == SUMMARY_BUTTONS

    replies = r.taps("resumen_nota")
    assert "Escribe la indicación" in replies[0].text
    replies = r.says("sin hielo porfa")
    assert button_ids(replies) == SUMMARY_BUTTONS
    assert r.order.notes == "sin hielo porfa"
    summary = replies[0].text
    assert "Torre 3 Apto 502" in summary and "$50.000" in summary and "sin hielo" in summary
    assert r.order.status == OrderStatus.BORRADOR

    replies = r.taps("resumen_ok")
    assert "pedido *#1* en la fila" in replies[0].text
    o = r.order
    assert o.status == OrderStatus.EN_COLA and o.queued_at is not None
    c = db.scalar(select(Customer))
    assert (c.torre, c.apto) == (3, "502")


def test_saved_address_shown_with_payment_question(db):
    r = Resident(db)
    order_until_summary(r)
    r.taps("resumen_ok")
    r.order.status = OrderStatus.ENTREGADO.value
    db.commit()

    r.says("buenas")
    replies = r.says("un huevo")
    # Modo ahorro: no pregunta la dirección aparte; la muestra con el pago [K1].
    assert len(replies) == 1
    assert "Te lo llevamos a *Torre 3 Apto 502*" in replies[0].text
    assert button_ids(replies) == PAYMENT_BUTTONS
    assert (r.order.torre, r.order.apto) == (3, "502")
    assert r.order.flow_step == FlowStep.PAGO


def test_other_address(db):
    r = Resident(db)
    order_until_summary(r)
    r.taps("resumen_ok")
    r.order.status = OrderStatus.ENTREGADO.value
    db.commit()

    r.says("hola")
    r.says("arroz")
    replies = r.says("otra dirección")
    assert "torre y apartamento" in replies[0].text
    replies = r.says("en la casa de mi mamá")
    assert "No entendí la dirección" in replies[0].text and "Torre 3 Apto 502" in replies[0].text
    r.says("T10-1104")
    assert (r.order.torre, r.order.apto) == (10, "1104")


def test_payment_by_text_instead_of_button(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    r.says("3-502")
    replies = r.says("ummm")
    assert button_ids(replies) == ["pago_efectivo", "pago_nequi", "pago_transf"]
    r.says("por nequi")
    assert r.order.payment_method == "nequi"
    assert r.order.flow_step == FlowStep.RESUMEN


def test_corregir_keeps_address_and_payment(db):
    r = Resident(db)
    order_until_summary(r, pay="pago_efectivo")
    replies = r.taps("resumen_edit")
    assert button_ids(replies) == ["edit_pedido", "edit_dir", "edit_pago"]
    replies = r.taps("edit_pedido")
    assert "pedido completo como debe quedar" in replies[0].text
    assert r.order.flow_step == FlowStep.PEDIDO

    replies = r.says("mejor 3 panes")
    # Vuelve directo al resumen conservando dirección y pago.
    assert button_ids(replies) == SUMMARY_BUTTONS
    o = r.order
    assert o.order_text == "mejor 3 panes"
    assert (o.torre, o.apto, o.payment_method, o.cash_amount) == (3, "502", "efectivo", 50_000)
    r.taps("resumen_ok")
    assert r.order.status == OrderStatus.EN_COLA


def test_reiniciar_discards_draft(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    first = r.order
    replies = r.says("Reiniciar")
    assert "Empecemos de nuevo" in replies[0].text
    assert db.get(Order, first.id).status == OrderStatus.CANCELADO
    assert r.order.id != first.id
    assert r.order.status == OrderStatus.BORRADOR
    assert r.order.flow_step == FlowStep.PEDIDO


def test_cancel_draft(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    replies = r.says("cancelar")
    assert "cancelé tu pedido" in replies[0].text
    assert r.order.status == OrderStatus.CANCELADO


def test_bot_silent_after_queue(db):
    r = Resident(db)
    order_until_summary(r)
    r.taps("resumen_ok")
    db.refresh(r.order)
    r.order.has_unread = False
    db.commit()

    replies = r.says("¿me agregas un pan?")
    assert replies == []
    o = r.order
    assert o.has_unread is True
    last = db.scalar(select(Message).order_by(Message.id.desc()))
    assert last.direction == "in" and last.order_id == o.id and last.text == "¿me agregas un pan?"


def test_cancel_in_queue_vs_later(db):
    r = Resident(db)
    order_until_summary(r)
    r.taps("resumen_ok")
    replies = r.says("cancelar")
    assert "cancelé" in replies[0].text
    assert r.order.status == OrderStatus.CANCELADO

    r2 = Resident(db, phone="+573009998877")
    order_until_summary(r2)
    r2.taps("resumen_ok")
    o = r2.order
    o.total, o.status = 10_000, OrderStatus.PREPARANDO.value
    db.commit()
    replies = r2.says("cancelar")
    assert "Le avisé a la tienda" in replies[0].text
    assert r2.order.status == OrderStatus.PREPARANDO
    assert r2.order.cancel_requested is True


def test_non_text_in_draft_and_after_queue(db):
    r = Resident(db)
    r.says("hola")
    replies = r.sends("audio")
    assert "solo puedo leer mensajes de texto" in replies[0].text
    assert r.order.flow_step == FlowStep.PEDIDO
    r.says("pan")
    r.says("t1 101")
    r.taps("pago_nequi")
    r.taps("resumen_ok")
    replies = r.sends("image")
    assert replies == []
    last = db.scalar(select(Message).order_by(Message.id.desc()))
    assert last.text == "[imagen recibida]"


def test_queue_positions_across_residents(db):
    a, b, c = (Resident(db, phone=f"+57300000000{i}") for i in range(3))
    for r in (a, b, c):
        r.says("hola")
    # b confirma primero aunque a escribió antes: el turno se asigna al confirmar.
    order_until_summary(b)
    assert "*#1*" in b.taps("resumen_ok")[0].text
    order_until_summary(a)
    assert "*#2*" in a.taps("resumen_ok")[0].text
    # c solo saludó: no ocupa puesto.
    assert c.order.queued_at is None


def test_duplicate_webhook_is_ignored(db):
    from app.services.inbox import process_incoming
    from app.services.whatsapp.base import IncomingMessage

    r = Resident(db)
    msg = IncomingMessage(r.phone, "text", text="hola", wa_message_id="wamid.DUP")
    assert process_incoming(db, msg, r.client, r.extractor) is True
    assert process_incoming(db, msg, r.client, r.extractor) is False
    assert db.scalar(select(func.count(Message.id)).where(Message.direction == "in")) == 1
    assert len(r.client.sent) == 1
    assert db.scalar(select(func.count(Order.id))) == 1


def test_closed_hours_block_new_orders(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "open_hour", 7)
    monkeypatch.setattr(settings, "close_hour", 22)
    r = Resident(db)
    night = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)  # 11 p. m. en Bogotá
    replies = r.says("hola", now=night)
    assert "cerrada" in replies[0].text
    assert db.scalar(select(func.count(Order.id))) == 0

    morning = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)  # 9 a. m. en Bogotá
    replies = r.says("hola", now=morning)
    assert "¿Qué quieres pedir?" in replies[0].text
    assert r.order.status == OrderStatus.BORRADOR


def test_draft_can_finish_after_closing(db, settings, monkeypatch):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    monkeypatch.setattr(settings, "open_hour", 7)
    monkeypatch.setattr(settings, "close_hour", 22)
    night = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)
    replies = r.says("t3 502", now=night)
    assert button_ids(replies) == ["pago_efectivo", "pago_nequi", "pago_transf"]
