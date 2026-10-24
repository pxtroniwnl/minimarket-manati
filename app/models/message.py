from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils import utcnow


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out | sys (nota interna, no se envía)
    sender: Mapped[str] = mapped_column(String(10))  # cliente | bot | tendero
    text: Mapped[str] = mapped_column(Text, default="")
    # Botones del mensaje saliente, para que el simulador los dibuje.
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    # Error de envío (p. ej. fuera de la ventana de 24h).
    send_error: Mapped[str | None] = mapped_column(String(255))
    # Entrantes: cuándo terminó de procesarlos el bot (None = pendiente, se reprocesa al arrancar).
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped["Order | None"] = relationship(back_populates="messages")  # noqa: F821
