from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StoreSetting(Base):
    """Ajustes que el tendero cambia desde el panel (p. ej. pausar pedidos)."""

    __tablename__ = "store_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
