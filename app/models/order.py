from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.flow.states import ACTIVE_STATUSES, FlowStep, OrderStatus
from app.utils import utcnow

_active_sql = "status IN ({})".format(", ".join(f"'{s.value}'" for s in ACTIVE_STATUSES))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # Máximo un pedido activo por cliente.
        Index(
            "uq_orders_one_active_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text(_active_sql),
            sqlite_where=text(_active_sql),
        ),
        Index("ix_orders_status_queued_at", "status", "queued_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_text: Mapped[str | None] = mapped_column(Text)
    torre: Mapped[int | None]
    apto: Mapped[str | None] = mapped_column(String(10))
    payment_method: Mapped[str | None] = mapped_column(String(20))
    cash_amount: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)
    total: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.BORRADOR.value)
    flow_step: Mapped[str] = mapped_column(String(20), default=FlowStep.PEDIDO.value)
    # El cliente pidió cancelar cuando ya no podía hacerlo solo; el tendero decide.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # Qué está corrigiendo desde el resumen (pedido | direccion | pago); al terminar vuelve al resumen.
    edit_target: Mapped[str | None] = mapped_column(String(20))
    # El cliente pidió hablar con la tienda durante el borrador: el bot se calla.
    needs_help: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ya se le avisó que es el siguiente en la fila.
    next_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    address_attempts: Mapped[int] = mapped_column(default=0)
    has_unread: Mapped[bool] = mapped_column(Boolean, default=False)
    # whatsapp = lo armó el bot · tienda = lo cargó el tendero desde el panel.
    source: Mapped[str] = mapped_column(String(10), default="whatsapp", server_default="whatsapp")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped["Customer"] = relationship(back_populates="orders")  # noqa: F821
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        back_populates="order", order_by="Message.id"
    )

    @property
    def address_label(self) -> str:
        if self.torre is None or not self.apto:
            return "—"
        return f"Torre {self.torre} Apto {self.apto}"

    @property
    def change_due(self) -> int | None:
        if self.payment_method != "efectivo" or self.cash_amount is None or self.total is None:
            return None
        return self.cash_amount - self.total
