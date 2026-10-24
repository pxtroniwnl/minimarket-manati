from datetime import timedelta

from app.flow.states import FlowStep, OrderStatus
from app.models import Customer, Order
from app.utils import utcnow


def make_customer(db, phone="+573001112233", **kw) -> Customer:
    c = Customer(phone=phone, **kw)
    db.add(c)
    db.flush()
    return c


def make_queued(db, phone, minutes_ago=0, status=OrderStatus.EN_COLA) -> Order:
    c = make_customer(db, phone=phone)
    o = Order(
        customer_id=c.id,
        order_text="pan y leche",
        torre=1,
        apto="101",
        payment_method="nequi",
        status=status.value,
        flow_step=FlowStep.RESUMEN.value,
        queued_at=utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(o)
    db.flush()
    return o


class Resident:
    """Simula a un residente chateando con el bot, por el mismo camino que el webhook."""

    def __init__(self, db, phone="+573001112233", name=None, client=None, extractor=None):
        from app.services.extractor import FallbackExtractor, RuleBasedExtractor
        from app.services.whatsapp.mock import MockWhatsAppClient

        self.db = db
        self.phone = phone
        self.name = name
        self.client = client or MockWhatsAppClient()
        self.extractor = extractor or FallbackExtractor(RuleBasedExtractor(), None)
        self._seq = 0

    def _send(self, msg, now=None) -> list:
        from app.services.inbox import process_incoming

        self.db.expire_all()  # como una request nueva: sin datos viejos en caché
        before = len(self.client.sent)
        process_incoming(self.db, msg, self.client, self.extractor, now=now)
        replies = [m for _, m in self.client.sent[before:]]
        for r in replies:
            assert_meta_limits(r)
        return replies

    def _id(self):
        import uuid

        return f"wamid.{uuid.uuid4().hex}"

    def says(self, text, now=None) -> list:
        from app.services.whatsapp.base import IncomingMessage

        return self._send(IncomingMessage(self.phone, "text", text=text, wa_message_id=self._id(), name=self.name), now)

    def taps(self, kind, now=None) -> list:
        """Toca un botón del pedido activo. `kind` sin ":" se completa con el id del pedido."""
        from app.services.whatsapp.base import IncomingMessage

        button_id = kind if ":" in kind else f"{kind}:{self.order.id}"
        return self._send(IncomingMessage(self.phone, "button", button_id=button_id, wa_message_id=self._id()), now)

    def sends(self, kind, now=None) -> list:
        from app.services.whatsapp.base import IncomingMessage

        return self._send(IncomingMessage(self.phone, kind, wa_message_id=self._id()), now)

    @property
    def order(self):
        from sqlalchemy import select

        from app.models import Customer, Order

        c = self.db.scalar(select(Customer).where(Customer.phone == self.phone))
        if c is None:
            return None
        return self.db.scalar(select(Order).where(Order.customer_id == c.id).order_by(Order.id.desc()))


def button_ids(replies) -> list[str]:
    """Tipos de botón (sin el sufijo ":<id del pedido>")."""
    return [b.id.split(":")[0] for r in replies for b in r.buttons]


def texts_of(replies) -> str:
    return "\n".join(r.text for r in replies)


def assert_meta_limits(message) -> None:
    """Invariante de la Cloud API: ≤3 botones, título ≤20 únicos, body con botones ≤1024, texto ≤4096."""
    if message.buttons:
        titles = [b.title for b in message.buttons]
        assert len(titles) <= 3, titles
        assert all(len(t) <= 20 for t in titles), titles
        assert len(set(titles)) == len(titles), titles
        assert all(len(b.id) <= 256 for b in message.buttons)
        assert 0 < len(message.text) <= 1024, len(message.text)
    else:
        assert 0 < len(message.text) <= 4096, len(message.text)


SUMMARY_BUTTONS = ["resumen_ok", "resumen_edit", "resumen_nota"]
PAYMENT_BUTTONS = ["pago_efectivo", "pago_nequi", "pago_transf"]
