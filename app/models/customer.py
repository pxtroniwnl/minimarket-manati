from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils import utcnow


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    torre: Mapped[int | None]
    apto: Mapped[str | None] = mapped_column(String(10))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Ya vio el aviso corto de tratamiento de datos (Ley 1581).
    privacy_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")  # noqa: F821

    @property
    def has_address(self) -> bool:
        return self.torre is not None and bool(self.apto)
