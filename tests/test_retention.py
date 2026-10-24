from datetime import timedelta

from sqlalchemy import func, select

from app.flow.states import OrderStatus
from app.models import Message, Order
from app.services.retention import purge_old_messages
from app.utils import utcnow
from tests.factories import Resident


def _age_all(db, days):
    for m in db.scalars(select(Message)):
        m.created_at = utcnow() - timedelta(days=days)
    db.commit()


def test_J2_old_messages_of_closed_orders_are_purged(db):
    r = Resident(db)
    r.says("hola")
    r.says("cancelar")
    _age_all(db, 100)
    assert purge_old_messages(db, days=90) > 0
    assert db.scalar(select(func.count(Message.id))) == 0
    assert db.scalar(select(func.count(Order.id))) == 1  # el pedido se conserva


def test_J2_recent_and_active_are_kept(db):
    r = Resident(db)
    r.says("hola")  # borrador activo
    _age_all(db, 100)
    assert purge_old_messages(db, days=90) == 0
    r2 = Resident(db, phone="+573000000099")
    r2.says("hola")
    r2.says("cancelar")  # reciente
    assert purge_old_messages(db, days=90) == 0


def test_J2_pending_inbound_is_never_purged(db):
    from app.models import Customer

    c = Customer(phone="+571")
    db.add(c)
    db.flush()
    db.add(Message(customer_id=c.id, direction="in", sender="cliente", text="x", created_at=utcnow() - timedelta(days=200)))
    db.commit()
    assert purge_old_messages(db, days=90) == 0
