import pytest
from sqlalchemy.exc import IntegrityError

from app.flow.machine import InvalidTransition, transition
from app.flow.states import FlowStep, OrderStatus
from app.models import Order
from tests.factories import make_customer


def _draft(db, **kw) -> Order:
    c = make_customer(db)
    o = Order(customer_id=c.id, **kw)
    db.add(o)
    db.flush()
    return o


def _complete(o: Order):
    o.order_text = "2 panes"
    o.torre, o.apto = 3, "502"
    o.payment_method = "nequi"
    o.flow_step = FlowStep.RESUMEN.value


def test_new_order_is_draft(db):
    o = _draft(db)
    assert o.status == OrderStatus.BORRADOR
    assert o.flow_step == FlowStep.PEDIDO
    assert o.queued_at is None


def test_draft_to_queue_requires_all_data(db):
    o = _draft(db)
    o.flow_step = FlowStep.RESUMEN.value
    with pytest.raises(InvalidTransition, match="faltan datos"):
        transition(o, OrderStatus.EN_COLA)
    assert o.status == OrderStatus.BORRADOR


def test_draft_to_queue_requires_confirmation_step(db):
    o = _draft(db)
    _complete(o)
    o.flow_step = FlowStep.NOTAS.value
    with pytest.raises(InvalidTransition, match="no ha confirmado"):
        transition(o, OrderStatus.EN_COLA)


def test_cash_requires_amount(db):
    o = _draft(db)
    _complete(o)
    o.payment_method = "efectivo"
    with pytest.raises(InvalidTransition, match="cash_amount"):
        transition(o, OrderStatus.EN_COLA)
    o.cash_amount = 50_000
    transition(o, OrderStatus.EN_COLA)
    assert o.status == OrderStatus.EN_COLA


def test_queue_sets_queued_at(db):
    o = _draft(db)
    _complete(o)
    transition(o, OrderStatus.EN_COLA)
    assert o.queued_at is not None


def test_full_happy_path_and_no_skipping(db):
    o = _draft(db)
    _complete(o)
    transition(o, OrderStatus.EN_COLA)
    with pytest.raises(InvalidTransition):
        transition(o, OrderStatus.PREPARANDO)
    with pytest.raises(InvalidTransition, match="total"):
        transition(o, OrderStatus.CONFIRMADO)
    o.total = 12_000
    for s in (OrderStatus.CONFIRMADO, OrderStatus.PREPARANDO, OrderStatus.EN_CAMINO, OrderStatus.ENTREGADO):
        transition(o, s)
    assert o.status == OrderStatus.ENTREGADO
    with pytest.raises(InvalidTransition):
        transition(o, OrderStatus.CANCELADO)


@pytest.mark.parametrize(
    "path",
    [
        [],
        [OrderStatus.EN_COLA],
        [OrderStatus.EN_COLA, OrderStatus.CONFIRMADO],
        [OrderStatus.EN_COLA, OrderStatus.CONFIRMADO, OrderStatus.PREPARANDO],
        [OrderStatus.EN_COLA, OrderStatus.CONFIRMADO, OrderStatus.PREPARANDO, OrderStatus.EN_CAMINO],
    ],
)
def test_cancel_from_any_state_before_delivered(db, path):
    o = _draft(db)
    _complete(o)
    o.total = 1000
    for s in path:
        transition(o, s)
    transition(o, OrderStatus.CANCELADO)
    assert o.status == OrderStatus.CANCELADO


def test_only_one_active_order_per_customer(db):
    c = make_customer(db)
    db.add(Order(customer_id=c.id))
    db.flush()
    db.add(Order(customer_id=c.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_closed_orders_do_not_block_new_one(db):
    c = make_customer(db)
    db.add(Order(customer_id=c.id, status=OrderStatus.ENTREGADO.value))
    db.add(Order(customer_id=c.id, status=OrderStatus.CANCELADO.value))
    db.add(Order(customer_id=c.id))
    db.flush()
