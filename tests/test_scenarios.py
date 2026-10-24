"""Un test por escenario del catálogo (docs/escenarios.md). El ID va en el nombre."""

from datetime import timedelta

from sqlalchemy import func, select

from app.flow.states import FlowStep, OrderStatus
from app.models import Message, Order
from app.utils import mask_phone, utcnow
from tests.factories import PAYMENT_BUTTONS, SUMMARY_BUTTONS, Resident, button_ids, texts_of


def summary_ready(r: Resident, order="2 panes", address="t3 502", pay="pago_nequi"):
    r.says("hola")
    r.says(order)
    r.says(address)
    replies = r.taps(pay)
    if pay == "pago_efectivo":
        replies = r.says("50")
    return replies


def queued(r: Resident, **kw):
    summary_ready(r, **kw)
    return r.taps("resumen_ok")


def deliver(db, r: Resident):
    r.order.status = OrderStatus.ENTREGADO.value
    db.commit()


# ---------------------------------------------------------------- A. pedido


def test_A2_first_message_with_greeting_and_order(db):
    r = Resident(db, name="Carlos")
    replies = r.says("hola vecino, me regala 2 panes y una leche")
    text = texts_of(replies)
    assert "¡Hola, Carlos!" in text
    assert "Anoté: _me regala 2 panes y una leche_" in text
    assert r.order.order_text == "me regala 2 panes y una leche"
    assert r.order.flow_step == FlowStep.DIR_ESCRIBIR


def test_A3_first_message_with_order_and_address(db):
    r = Resident(db)
    replies = r.says("2 panes pa la t3 502")
    assert len(replies) == 1
    assert "Te lo llevamos a *Torre 3 Apto 502*" in replies[0].text
    assert button_ids(replies) == PAYMENT_BUTTONS
    assert (r.order.torre, r.order.apto) == (3, "502")
    assert r.order.flow_step == FlowStep.PAGO


def test_A3_detected_address_overrides_saved_one(db):
    r = Resident(db)
    queued(r, address="t1 101")
    deliver(db, r)
    replies = r.says("una leche para la torre 4 apto 1101")
    assert "Te lo llevamos a *Torre 4 Apto 1101*" in replies[-1].text
    assert (r.order.torre, r.order.apto) == (4, "1101")


def test_A4_split_message_at_address_step(db):
    r = Resident(db)
    r.says("hola")
    r.says("2 panes")
    replies = r.says("y una leche")
    assert "Agregué _y una leche_ a tu pedido 👍" in replies[0].text
    assert "torre y apartamento" in replies[1].text
    replies = r.says("ah y hielo")
    assert r.order.order_text == "2 panes + y una leche + ah y hielo"
    r.says("t3 502")
    assert r.order.flow_step == FlowStep.PAGO


def test_A4_split_message_with_saved_address(db):
    r = Resident(db)
    queued(r)
    deliver(db, r)
    r.says("hola")
    r.says("arroz")
    replies = r.says("y dos huevos")
    assert "Agregué" in replies[0].text
    assert button_ids(replies) == PAYMENT_BUTTONS
    assert r.order.order_text == "arroz + y dos huevos"


def test_A4_split_message_at_payment_step(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    r.says("t3 502")
    replies = r.says("y una gaseosa de litro")
    assert "Agregué" in replies[0].text
    assert button_ids(replies) == ["pago_efectivo", "pago_nequi", "pago_transf"]


def test_A5_greeting_or_emoji_is_not_an_order(db):
    r = Resident(db)
    r.says("hola")
    for text in ("hola", "buenas", "👍", "ok", "?"):
        replies = r.says(text)
        assert "Escríbeme tu pedido" in replies[0].text, text
        assert r.order.order_text is None


def test_A6_question_offers_choice_then_help(db):
    r = Resident(db)
    replies = r.says("¿tienen pan integral?")
    assert button_ids(replies) == ["es_pedido", "preguntar"]
    replies = r.taps("preguntar")
    assert "le paso tu mensaje a la tienda" in replies[0].text
    o = r.order
    assert o.needs_help and o.has_unread and o.status == OrderStatus.BORRADOR


def test_A6_question_was_actually_an_order(db):
    r = Resident(db)
    r.says("hola")
    replies = r.says("hay pan tajado? me manda 2")
    assert button_ids(replies) == ["es_pedido", "preguntar"]
    replies = r.taps("es_pedido")
    assert r.order.order_text == "hay pan tajado? me manda 2"
    assert "torre y apartamento" in replies[0].text


def test_A7_long_order_is_clipped_only_in_summary(db):
    long_order = "1 paquete de arroz, " * 80  # ~1600 caracteres
    r = Resident(db)
    replies = summary_ready(r, order=long_order.strip())
    assert len(r.order.order_text) > 1500  # se guarda completo
    assert "…" in replies[0].text
    assert len(replies[0].text) <= 1024


# ------------------------------------------------------------- B. dirección


def test_B2_tower_does_not_exist(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    replies = r.says("torre 12 apto 502")
    assert "torres de la 1 a la 10" in replies[0].text


def test_B3_floor_does_not_exist(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    replies = r.says("t2 905")
    assert "La torre 2 tiene 7 pisos (apartamentos 101 a 704)" in replies[0].text


def test_B4_apartment_does_not_exist(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    replies = r.says("t3 507")
    assert "por ejemplo 501 a 504" in replies[0].text


def test_B6_thumbs_up_confirms_saved_address(db):
    r = Resident(db)
    queued(r)
    deliver(db, r)
    r.says("hola")
    r.says("pan")
    r.says("👍")
    assert r.order.flow_step == FlowStep.PAGO


def test_B7_three_failed_attempts_offer_help(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    r.says("t3 9999")
    r.says("en la casa de mi mamá")
    replies = r.says("t15 101")
    assert button_ids(replies) == ["ayuda"]
    replies = r.taps("ayuda")
    assert "le paso tu mensaje a la tienda" in replies[0].text
    assert r.order.needs_help


# ------------------------------------------------------------------ C. pago


def test_C3_absurd_cash_amount_is_rejected(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    r.says("t3 502")
    r.taps("pago_efectivo")
    replies = r.says("500000000")
    assert "en números" in replies[0].text
    assert r.order.cash_amount is None


# ------------------------------------------------------- D. resumen/corregir


def test_D1_correct_only_the_address(db):
    r = Resident(db)
    summary_ready(r, pay="pago_efectivo")
    r.taps("resumen_edit")
    replies = r.taps("edit_dir")
    assert "torre y apartamento" in replies[0].text
    replies = r.says("t4 1101")
    assert button_ids(replies) == SUMMARY_BUTTONS
    o = r.order
    assert (o.torre, o.apto, o.order_text, o.cash_amount) == (4, "1101", "2 panes", 50_000)


def test_D1_correct_only_the_order(db):
    r = Resident(db)
    summary_ready(r)
    r.taps("resumen_edit")
    r.taps("edit_pedido")
    replies = r.says("3 panes y un queso")
    assert button_ids(replies) == SUMMARY_BUTTONS
    assert r.order.order_text == "3 panes y un queso"
    assert (r.order.torre, r.order.payment_method) == (3, "nequi")


def test_D2_text_at_summary_is_appended(db):
    r = Resident(db)
    summary_ready(r)
    replies = r.says("ah, y un hielo")
    assert "Agregué _ah, y un hielo_" in replies[0].text
    assert "2 panes + ah, y un hielo" in replies[1].text
    assert button_ids(replies) == SUMMARY_BUTTONS
    assert r.order.flow_step == FlowStep.RESUMEN


def test_D3_change_payment_to_cash_skips_notes(db):
    r = Resident(db)
    summary_ready(r)
    r.says("sin cebolla")  # se agrega al pedido (D2)
    r.taps("resumen_edit")
    r.taps("edit_pago")
    replies = r.taps("pago_efectivo")
    assert "¿Con cuánto pagas?" in replies[0].text
    replies = r.says("20 mil")
    assert button_ids(replies) == SUMMARY_BUTTONS
    assert (r.order.payment_method, r.order.cash_amount) == ("efectivo", 20_000)


def test_D3_change_cash_to_nequi_clears_amount(db):
    r = Resident(db)
    summary_ready(r, pay="pago_efectivo")
    r.taps("resumen_edit")
    r.taps("edit_pago")
    r.taps("pago_nequi")
    assert (r.order.payment_method, r.order.cash_amount) == ("nequi", None)
    assert r.order.flow_step == FlowStep.RESUMEN


def test_D4_old_button_from_previous_step_repeats_current_question(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    r.says("t3 502")
    r.taps("pago_nequi")  # ahora está en el RESUMEN
    replies = r.taps("pago_efectivo")  # botón viejo del paso de pago
    assert r.order.payment_method == "nequi"
    # Botón de pago de este mismo pedido: el resumen no lo entiende y se repite.
    assert button_ids(replies) == SUMMARY_BUTTONS


def test_D4_button_from_previous_order_is_ignored(db):
    r = Resident(db)
    queued(r)
    old_id = r.order.id
    deliver(db, r)
    r.says("hola")
    replies = r.taps(f"resumen_ok:{old_id}")
    assert r.order.status == OrderStatus.BORRADOR
    assert "¿Qué quieres pedir?" in replies[0].text


def test_D4_old_button_without_active_order_does_not_open_one(db):
    r = Resident(db)
    queued(r)
    old_id = r.order.id
    deliver(db, r)
    count = db.scalar(select(func.count(Order.id)))
    replies = r.taps(f"resumen_ok:{old_id}")
    assert "No tienes pedidos en curso" in replies[0].text
    assert db.scalar(select(func.count(Order.id))) == count


def test_D5_double_tap_confirm(db):
    r = Resident(db)
    summary_ready(r)
    r.taps("resumen_ok")
    r.order.has_unread = False
    db.commit()
    replies = r.taps("resumen_ok")
    assert replies == []
    assert r.order.has_unread is False
    assert r.order.status == OrderStatus.EN_COLA


# --------------------------------------------------------------- E. la cola


def test_E4_cancel_request_gets_an_answer(db):
    r = Resident(db)
    queued(r)
    r.order.total, r.order.status = 10_000, OrderStatus.EN_CAMINO.value
    db.commit()
    replies = r.says("cancelar")
    assert "Le avisé a la tienda" in replies[0].text
    assert r.order.cancel_requested and r.order.status == OrderStatus.EN_CAMINO


def test_E5_same_instant_gets_different_positions(db):
    from app.services.queue import queue_position
    from tests.factories import make_queued

    a = make_queued(db, "+571")
    b = make_queued(db, "+572")
    b.queued_at = a.queued_at
    db.flush()
    assert {queue_position(db, a), queue_position(db, b)} == {1, 2}


def test_E6_next_in_line_is_notified_once(db):
    a = Resident(db, phone="+573000000001")
    b = Resident(db, phone="+573000000002", client=a.client)
    queued(a)
    queued(b)
    assert "*#2*" in b.client.sent[-1][1].text
    before = len(a.client.sent)
    a.says("cancelar")  # a sale de la fila → b queda de primero
    sent = a.client.sent[before:]
    to_b = [m for phone, m in sent if phone == b.phone]
    assert len(to_b) == 1 and "Eres el siguiente" in to_b[0].text
    assert b.order.next_notified
    # No se repite.
    c = Resident(db, phone="+573000000003", client=a.client)
    before = len(a.client.sent)
    queued(c)
    assert all(phone != b.phone for phone, _ in a.client.sent[before:])


def test_E7_first_in_line_is_not_notified_twice(db):
    a = Resident(db)
    replies = queued(a)
    assert "*#1*" in replies[0].text
    assert a.order.next_notified
    assert not any("Eres el siguiente" in m.text for _, m in a.client.sent)


def test_E8_reactions_are_ignored(db):
    r = Resident(db)
    assert r.sends("reaction") == []
    assert db.scalar(select(func.count(Order.id))) == 0
    queued(r)
    r.order.has_unread = False
    db.commit()
    assert r.sends("reaction") == []
    assert r.order.has_unread is False


# ---------------------------------------------------- F. sin pedido / viejos


def test_F1_cancel_or_restart_without_order(db):
    r = Resident(db)
    for word in ("cancelar", "reiniciar"):
        replies = r.says(word)
        assert "No tienes pedidos en curso" in replies[0].text
    assert db.scalar(select(func.count(Order.id))) == 0


def test_F2_thanks_after_delivery_does_not_open_order(db):
    r = Resident(db)
    queued(r)
    deliver(db, r)
    count = db.scalar(select(func.count(Order.id)))
    replies = r.says("muchas gracias!")
    assert "Con gusto" in replies[0].text
    assert db.scalar(select(func.count(Order.id))) == count


def test_F2_thanks_long_after_is_a_normal_greeting(db):
    r = Resident(db)
    queued(r)
    r.order.status = OrderStatus.ENTREGADO.value
    r.order.updated_at = utcnow() - timedelta(days=1)
    db.commit()
    replies = r.says("gracias", now=utcnow())
    assert "¿Qué quieres pedir?" in replies[0].text


def test_F3_abandoned_draft_starts_over(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    old = r.order
    old.updated_at = utcnow() - timedelta(hours=3)
    db.commit()
    replies = r.says("hola", now=utcnow())
    assert "quedó sin terminar" in replies[0].text
    assert db.get(Order, old.id).status == OrderStatus.CANCELADO
    assert r.order.id != old.id and r.order.flow_step == FlowStep.PEDIDO


def test_F3_abandoned_draft_with_new_order_in_message(db):
    r = Resident(db)
    r.says("hola")
    r.order.updated_at = utcnow() - timedelta(hours=3)
    db.commit()
    replies = r.says("me manda un hielo", now=utcnow())
    assert "quedó sin terminar" in replies[0].text
    assert r.order.order_text == "me manda un hielo"


# ---------------------------------------------------- G. hablar con la tienda


def test_G1_help_keyword_silences_the_bot(db):
    r = Resident(db)
    r.says("hola")
    r.says("pan")
    replies = r.says("hablar con la tienda")
    assert "le paso tu mensaje a la tienda" in replies[0].text
    assert r.order.needs_help
    r.order.has_unread = False
    db.commit()
    assert r.says("¿a qué hora cierran?") == []
    assert r.order.has_unread is True
    assert r.order.status == OrderStatus.BORRADOR


def test_G1_help_draft_does_not_expire(db):
    r = Resident(db)
    r.says("hola")
    r.says("ayuda")
    r.order.updated_at = utcnow() - timedelta(hours=5)
    db.commit()
    assert r.says("sigo esperando", now=utcnow()) == []
    assert r.order.needs_help


# ---------------------------------------------------------- H. robustez


def test_H2_unexpected_error_apologizes_and_keeps_message(db, monkeypatch):
    from app.flow.bot import Bot

    r = Resident(db)
    r.says("hola")

    def boom(*a, **kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(Bot, "handle", boom)
    replies = r.says("2 panes")
    assert "tuvimos un problema" in replies[0].text
    inbound = db.scalar(select(Message).where(Message.text == "2 panes"))
    assert inbound is not None and inbound.processed_at is not None
    monkeypatch.undo()
    # El cliente reintenta y todo sigue normal.
    r.says("2 panes")
    assert r.order.order_text == "2 panes"


def test_H3_pending_messages_are_reprocessed(db):
    from app.models import Customer
    from app.services.inbox import reprocess_pending

    r = Resident(db)
    r.says("hola")
    c = db.scalar(select(Customer))
    # Simula un entrante guardado justo antes de un reinicio.
    db.add(Message(customer_id=c.id, direction="in", sender="cliente", text="2 panes", payload_json={"type": "text"}))
    db.commit()
    assert reprocess_pending(db, r.client, r.extractor) == 1
    assert r.order.order_text == "2 panes"
    assert reprocess_pending(db, r.client, r.extractor) == 0


def test_H8_long_message_with_buttons_is_split():
    from app.services.outbox import MAX_INTERACTIVE_BODY, MAX_TEXT, fit_limits, split_text
    from app.services.whatsapp.base import Button, OutgoingMessage

    parts = fit_limits(OutgoingMessage("x" * 1500, [Button("a:1", "A")]))
    assert [bool(p.buttons) for p in parts] == [False, True]
    assert all(len(p.text) <= MAX_INTERACTIVE_BODY or not p.buttons for p in parts)
    chunks = split_text(("palabra " * 1500).strip())
    assert len(chunks) == 3 and all(len(c) <= MAX_TEXT for c in chunks)
    assert " ".join(chunks).split() == ["palabra"] * 1500


def test_H9_rate_limit(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    r = Resident(db)
    answered = [bool(r.says(f"mensaje {i}")) for i in range(5)]
    assert answered == [True, True, True, False, False]
    assert db.scalar(select(func.count(Message.id)).where(Message.direction == "in")) == 5


# --------------------------------------------------- J. datos y privacidad


def test_J1_privacy_notice_only_first_time(db):
    r = Resident(db)
    assert "solo para gestionar tus pedidos" in r.says("hola")[0].text
    r.says("cancelar")
    assert "solo para gestionar tus pedidos" not in r.says("hola")[0].text


def test_J4_phone_masking():
    assert mask_phone("+573001112233") == "+57300***2233"


# ------------------------------------------- K. ahorro de mensajes (Meta cobra desde el 1.001)


def test_K1_change_address_at_payment_step(db):
    r = Resident(db)
    queued(r)
    deliver(db, r)
    r.says("hola")
    r.says("un hielo")
    replies = r.says("no, es la t4 1101")
    assert "Listo, te lo llevamos a *Torre 4 Apto 1101*" in replies[0].text
    assert button_ids(replies) == PAYMENT_BUTTONS
    assert (r.order.torre, r.order.apto) == (4, "1101")


def test_K1_invalid_address_at_payment_step(db):
    r = Resident(db)
    queued(r)
    deliver(db, r)
    r.says("hola")
    r.says("un hielo")
    replies = r.says("t2 905")
    assert "La torre 2 tiene 7 pisos" in replies[0].text
    assert (r.order.torre, r.order.apto) == (3, "502")


def test_K2_optional_note_from_summary(db):
    r = Resident(db)
    summary_ready(r)
    r.taps("resumen_nota")
    replies = r.says("dejar en portería")
    assert "dejar en portería" in replies[0].text
    assert r.order.notes == "dejar en portería"
    assert button_ids(replies) == SUMMARY_BUTTONS


def test_K2_note_button_then_nothing_keeps_previous_note(db):
    r = Resident(db)
    summary_ready(r)
    r.taps("resumen_nota")
    r.says("tocar duro")
    r.taps("resumen_nota")
    r.says("no")
    assert r.order.notes == "tocar duro"


def _total_messages(r: Resident, db) -> int:
    from app.flow.states import OrderStatus
    from app.services import orders

    o = r.order
    orders.set_total(db, r.client, o, "12000")
    for st in (OrderStatus.PREPARANDO, OrderStatus.EN_CAMINO, OrderStatus.ENTREGADO):
        orders.change_status(db, r.client, o, st)
    db.commit()
    return len(r.client.sent)


def test_K_message_budget_new_customer(db):
    r = Resident(db)
    queued(r)  # hola · pedido · dirección · pago → resumen · confirmar
    assert _total_messages(r, db) <= 7


def test_K_message_budget_returning_customer(db):
    from app.services.whatsapp.mock import MockWhatsAppClient

    r = Resident(db)
    queued(r)
    deliver(db, r)
    r.client = MockWhatsAppClient()
    r.says("2 panes y una leche")  # pedido + dirección guardada + pago en un mensaje
    r.taps("pago_nequi")
    r.taps("resumen_ok")
    assert _total_messages(r, db) <= 6


def test_K3_optional_status_notices(db, settings, monkeypatch):
    from app.flow.states import OrderStatus
    from app.services import orders

    r = Resident(db)
    queued(r)
    orders.set_total(db, r.client, r.order, "5000")
    sent = len(r.client.sent)
    orders.change_status(db, r.client, r.order, OrderStatus.PREPARANDO)
    assert len(r.client.sent) == sent  # apagado por defecto
    monkeypatch.setattr(settings, "notify_preparing", True)
    monkeypatch.setattr(settings, "notify_delivered", True)
    orders.change_status(db, r.client, r.order, OrderStatus.EN_CAMINO)
    orders.change_status(db, r.client, r.order, OrderStatus.ENTREGADO)
    assert "va en camino" in r.client.sent[-2][1].text
    assert "Pedido entregado" in r.client.sent[-1][1].text


def test_K4_monthly_usage_counter(db, settings, monkeypatch):
    from datetime import timedelta

    from app.services.usage import monthly_usage

    monkeypatch.setattr(settings, "wa_free_tier", 10)
    r = Resident(db)
    queued(r)
    usage = monthly_usage(db)
    assert usage.sent == len(r.client.sent)
    # Los de meses anteriores y los fallidos no cuentan.
    outs = db.scalars(select(Message).where(Message.direction == "out").order_by(Message.id)).all()
    old, failed = outs[0], outs[-1]
    old.created_at = utcnow() - timedelta(days=40)
    failed.send_error = "No entregado"
    db.commit()
    assert monthly_usage(db).sent == usage.sent - 2


def test_K4_usage_levels():
    from app.services.usage import Usage

    assert [Usage(n, 1000).level for n in (0, 799, 800, 999, 1000, 1500)] == ["ok", "ok", "warn", "warn", "over", "over"]
