from app.flow.states import OrderStatus
from app.services.queue import active_queue, positions, queue_position
from tests.factories import make_queued


def test_position_follows_queued_at(db):
    a = make_queued(db, "+571", minutes_ago=10)
    b = make_queued(db, "+572", minutes_ago=5)
    c = make_queued(db, "+573", minutes_ago=1)
    assert [queue_position(db, o) for o in (a, b, c)] == [1, 2, 3]


def test_cancellation_in_the_middle_moves_everyone_up(db):
    a = make_queued(db, "+571", minutes_ago=10)
    b = make_queued(db, "+572", minutes_ago=5)
    c = make_queued(db, "+573", minutes_ago=1)
    b.status = OrderStatus.CANCELADO.value
    db.flush()
    assert queue_position(db, a) == 1
    assert queue_position(db, b) is None
    assert queue_position(db, c) == 2


def test_confirmed_and_preparing_count_but_on_the_way_does_not(db):
    make_queued(db, "+571", minutes_ago=30, status=OrderStatus.EN_CAMINO)
    make_queued(db, "+572", minutes_ago=20, status=OrderStatus.PREPARANDO)
    make_queued(db, "+573", minutes_ago=10, status=OrderStatus.CONFIRMADO)
    d = make_queued(db, "+574", minutes_ago=1)
    assert queue_position(db, d) == 3


def test_delivered_frees_the_spot(db):
    a = make_queued(db, "+571", minutes_ago=10)
    b = make_queued(db, "+572", minutes_ago=5)
    a.status = OrderStatus.ENTREGADO.value
    db.flush()
    assert queue_position(db, b) == 1


def test_active_queue_order_and_positions_match(db):
    c = make_queued(db, "+573", minutes_ago=1)
    a = make_queued(db, "+571", minutes_ago=10)
    w = make_queued(db, "+570", minutes_ago=20, status=OrderStatus.EN_CAMINO)
    b = make_queued(db, "+572", minutes_ago=5)
    q = active_queue(db)
    assert [o.id for o in q] == [w.id, a.id, b.id, c.id]
    pos = positions(q)
    assert w.id not in pos
    assert all(pos[o.id] == queue_position(db, o) for o in (a, b, c))
