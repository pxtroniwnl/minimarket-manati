"""Carga 3 pedidos de ejemplo para ver el panel con datos (solo desarrollo, modo mock).

    uv run python -m scripts.seed
"""

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Order
from app.services import orders
from app.services.inbox import process_incoming
from app.services.whatsapp.base import IncomingMessage
from app.services.whatsapp.mock import MockWhatsAppClient

_seq = 0


def say(db, client, phone, text=None, button=None, name=None):
    global _seq
    _seq += 1
    if button:
        order = db.scalar(select(Order).where(Order.customer.has(phone=phone)).order_by(Order.id.desc()))
        msg = IncomingMessage(phone, "button", button_id=f"{button}:{order.id}", wa_message_id=f"seed.{phone}.{_seq}")
    else:
        msg = IncomingMessage(phone, "text", text=text, wa_message_id=f"seed.{phone}.{_seq}", name=name)
    process_incoming(db, msg, client)


def main() -> None:
    if get_settings().whatsapp_mode != "mock":
        raise SystemExit("El seed es solo para desarrollo (WHATSAPP_MODE=mock).")
    client = MockWhatsAppClient()
    with SessionLocal() as db:
        if db.scalar(select(Order.id).limit(1)):
            raise SystemExit("La base ya tiene pedidos; el seed solo corre sobre una base vacía.")

        # 1) En cola, efectivo, con nota.
        a = "+573001110001"
        say(db, client, a, "hola vecina, me regala 2 panes tajados, 1 leche entera y 1 bolsa de hielo", name="Ana Pérez")
        say(db, client, a, "t3 502")
        say(db, client, a, button="pago_efectivo")
        say(db, client, a, "50 mil")
        say(db, client, a, button="resumen_nota")
        say(db, client, a, "tocar duro, el timbre no sirve")
        say(db, client, a, button="resumen_ok")

        # 2) Confirmado con total, Nequi, y un mensaje sin leer.
        b = "+573001110002"
        say(db, client, b, "buenas, 1 coca cola 1.5 y unas papas de limón pa la torre 7 apto 1103", name="Luis")
        say(db, client, b, button="pago_nequi")
        say(db, client, b, button="resumen_ok")
        order_b = db.scalar(select(Order).where(Order.customer.has(phone=b)))
        orders.set_total(db, client, order_b, "9.800")
        db.commit()
        say(db, client, b, "me agregas un chicle porfa?")

        # 3) Consulta para la tienda.
        say(db, client, "+573001110003", "¿tienen pan integral?", name="Marta")
        say(db, client, "+573001110003", button="preguntar")

    print("Listo: 2 pedidos en la fila y 1 consulta. Abre /panel.")


if __name__ == "__main__":
    main()
