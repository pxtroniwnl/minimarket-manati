"""Pedidos que el tendero carga desde el panel (llamada o en persona) [L1–L5]."""

from sqlalchemy import select

from app.flow.states import OrderStatus
from app.models import Customer, Message, Order
from tests.factories import Resident

FORM = {
    "name": "Doña Marta",
    "phone": "",
    "order_text": "1 libra de arroz y 2 huevos",
    "address": "t4 1101",
    "payment_method": "nequi",
    "cash_amount": "",
    "notes": "",
    "total": "",
}


def create(panel, **changes):
    return panel.post("/panel/nuevo", data={**FORM, **changes}, headers={"HX-Request": "true"})


def only_order(db) -> Order:
    db.expire_all()
    return db.scalars(select(Order)).one()


def test_L1_new_order_goes_straight_to_the_queue(panel, db):
    assert "Nuevo pedido" in panel.get("/panel").text
    assert panel.get("/panel/nuevo").status_code == 200
    r = create(panel)
    order = only_order(db)
    assert r.status_code == 204 and r.headers["HX-Redirect"] == f"/panel/pedido/{order.id}"
    assert order.status == OrderStatus.EN_COLA and order.queued_at is not None
    assert (order.source, order.torre, order.apto) == ("tienda", 4, "1101")
    assert order.customer.phone is None and order.customer.name == "Doña Marta"
    assert "🏪" in panel.get("/panel/cola").text
    assert panel.mock.sent == []  # nunca se inicia una conversación (costaría plantillas)


def test_L1_total_known_upfront_confirms_it(panel, db):
    create(panel, total="12 mil")
    order = only_order(db)
    assert order.status == OrderStatus.CONFIRMADO and order.total == 12000


def test_L2_validation_errors_keep_what_was_typed(panel, db):
    r = create(panel, address="t2 905")
    assert "La torre 2 tiene 7 pisos" in r.text and "1 libra de arroz" in r.text
    r = create(panel, order_text="  ")
    assert "no puede quedar vacío" in r.text
    r = create(panel, payment_method="efectivo")
    assert "con cuánto paga" in r.text
    r = create(panel, phone="123")
    assert "no parece un celular" in r.text
    assert db.scalars(select(Order)).all() == []


def test_L3_no_whatsapp_order_never_calls_meta(panel, db):
    create(panel, total="8.000")
    order = only_order(db)
    detail = panel.get(f"/panel/pedido/{order.id}").text
    assert "sin WhatsApp" in detail and "Escríbele al cliente" not in detail
    panel.post(f"/panel/pedido/{order.id}/estado", data={"status": "preparando", "expected": "confirmado"})
    panel.post(f"/panel/pedido/{order.id}/estado", data={"status": "en_camino", "expected": "preparando"})
    assert only_order(db).status == OrderStatus.EN_CAMINO
    assert panel.mock.sent == []
    errors = db.scalars(select(Message.send_error).where(Message.direction == "out")).all()
    assert errors and all("sin WhatsApp" in e for e in errors)


def test_L4_phone_links_with_whatsapp_and_saves_address(panel, db):
    create(panel, phone="300 555 0101")
    order = only_order(db)
    customer = db.scalars(select(Customer)).one()
    assert customer.phone == "+573005550101" and (customer.torre, customer.apto) == (4, "1101")
    # Luego escribe por WhatsApp: el bot no arma otro pedido, el mensaje va al panel.
    r = Resident(db, phone="+573005550101")
    r.says("vecino, agréguele una gaseosa")
    db.expire_all()
    assert db.get(Order, order.id).has_unread
    assert db.scalars(select(Order)).all() == [db.get(Order, order.id)]


def test_L5_customer_with_active_order_is_rejected(panel, db):
    r = Resident(db, phone="+573005550102")
    r.says("hola")
    r.says("2 panes")
    r.says("t3 502")
    r.taps("pago_nequi")
    r.taps("resumen_ok")
    resp = create(panel, phone="3005550102")
    assert "ya tiene un pedido en curso" in resp.text
    assert len(db.scalars(select(Order)).all()) == 1


def test_L1_requires_login(client):
    r = client.post("/panel/nuevo", data=FORM, follow_redirects=False)
    assert r.status_code == 303
