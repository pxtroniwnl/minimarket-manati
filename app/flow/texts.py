"""Todos los textos que envía el bot."""

from app.config import get_settings
from app.flow.states import PaymentMethod
from app.models import Order
from app.utils import pesos

EXAMPLE_ADDRESS = "Torre 3 Apto 502"
SUMMARY_ORDER_MAX = 600  # el body de un mensaje con botones admite 1024 caracteres


def greeting(name: str | None) -> str:
    hola = f"¡Hola, {name.split()[0]}! 👋" if name else "¡Hola! 👋"
    return f"{hola} Bienvenido a *{get_settings().store_name}*."


PRIVACY = "_Usamos tu número y tu dirección solo para gestionar tus pedidos._"

ASK_ORDER = "¿Qué quieres pedir? Escríbelo todo en un mensaje 🛒"
ASK_ORDER_EDIT = "Dale, escribe tu pedido completo como debe quedar 🛒"
ASK_ORDER_AGAIN = (
    "Escríbeme tu pedido en un mensaje de texto, por ejemplo: "
    "_2 panes, 1 leche y 1 Coca-Cola de 1,5L_ 🛒"
)


def noted(order_text: str) -> str:
    return f"Anoté: _{order_text}_ 📝"


def appended(extra: str) -> str:
    return f"Agregué _{extra}_ a tu pedido 👍"


def confirm_address(torre: int, apto: str) -> str:
    return f"¿Te lo llevamos a *Torre {torre} Apto {apto}*? 📍"


ASK_ADDRESS = f"¿A qué torre y apartamento te lo llevamos? 📍\nEjemplo: *{EXAMPLE_ADDRESS}*"
ADDRESS_RETRY = (
    f"No entendí la dirección 😅 Escríbela así: *{EXAMPLE_ADDRESS}* (también sirve *T3-502*)."
)


def address_invalid(reason: str) -> str:
    return f"Mmm, esa dirección no existe en el conjunto 🤔 {reason}\nEscríbela así: *{EXAMPLE_ADDRESS}*"


ADDRESS_HELP_OFFER = "Si prefieres, elige la opción de abajo y te atiende alguien de la tienda 🙋"
ASK_PAYMENT = "¿Cómo vas a pagar? 💳"
ASK_CASH = "¿Con cuánto pagas? 💵 (así te llevamos el cambio)\nEjemplo: *50 mil* o *50.000*"
CASH_RETRY = "Escríbeme el monto en números, por ejemplo *20.000* o *20 mil* 🙏"
ASK_NOTE = "Escribe la indicación para la entrega (ej. _tocar duro_, _dejar en portería_) 🗒️"


def deliver_to(torre: int, apto: str) -> str:
    return f"📍 Te lo llevamos a *Torre {torre} Apto {apto}*. Si es otra dirección, escríbela."


def address_changed(torre: int, apto: str) -> str:
    return f"Listo, te lo llevamos a *Torre {torre} Apto {apto}* 📍"
EDITED = "Vi que editaste un mensaje, pero no alcanzo a ver la edición 🙏 ¿Me lo escribes de nuevo?"
ONLY_TEXT = "Por ahora solo puedo leer mensajes de texto 🙏 ¿Me lo escribes?"
CANCELLED = "Listo, cancelé tu pedido ❌. Cuando quieras pedir algo, escríbeme."
CANCEL_REQUESTED = (
    "Le avisé a la tienda que quieres cancelar 🙏 Como tu pedido ya está en proceso, "
    "ellos te confirman por aquí."
)
RESTARTED = "Empecemos de nuevo 🔄"
EXPIRED = "Tu pedido anterior quedó sin terminar, así que empecemos de nuevo 🔄"
NO_ACTIVE_ORDER = "No tienes pedidos en curso 🙂 Cuando quieras pedir algo, escríbeme."
COURTESY_REPLY = "¡Con gusto! 🙌 Aquí estamos cuando nos necesites."
NOT_UNDERSTOOD_BUTTONS = "Elige una de las opciones de abajo 👇"
IS_IT_AN_ORDER = "¿Esto es tu pedido o una pregunta para la tienda? 🤔"
HANDOFF_ACK = (
    "📩 Le pasé tu mensaje a la tienda, te responden por aquí. "
    "Si es un pedido nuevo, lo armamos apenas te entreguemos el actual."
)
HELP = "Listo, le paso tu mensaje a la tienda 🙋 Te responden por aquí en un momento."
EDIT_MENU = "¿Qué quieres corregir? ✏️"
NEXT_IN_LINE = "¡Eres el siguiente! 🎉 Tu pedido es el próximo que atiende la tienda."
PAUSED = "En este momento no estamos recibiendo pedidos 🙏 Escríbenos un poco más tarde."
APOLOGY = "Uy, tuvimos un problema 😓 Intenta de nuevo en un momento, por favor."
CHOOSE_OPTION = "Elige una opción 👇"

PAYMENT_LABEL = {
    PaymentMethod.EFECTIVO: "Efectivo",
    PaymentMethod.NEQUI: "Nequi",
    PaymentMethod.TRANSFERENCIA: "Transferencia",
}


def closed() -> str:
    s = get_settings()
    return (
        f"Hola 👋 *{s.store_name}* está cerrada en este momento. "
        f"Atendemos de {s.open_hour}:00 a {s.close_hour}:00. ¡Te esperamos!"
    )


def payment_line(order: Order) -> str:
    label = PAYMENT_LABEL[PaymentMethod(order.payment_method)]
    if order.payment_method == PaymentMethod.EFECTIVO and order.cash_amount:
        return f"{label} (pagas con {pesos(order.cash_amount)})"
    return label


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def summary(order: Order) -> str:
    lines = [
        "📝 *Resumen de tu pedido*",
        "",
        f"🛒 {_clip(order.order_text or '', SUMMARY_ORDER_MAX)}",
        f"📍 {order.address_label}",
        f"💳 {payment_line(order)}",
    ]
    if order.notes:
        lines.append(f"🗒️ {_clip(order.notes, 150)}")
    lines += ["", "¿Está todo bien? Si te faltó algo, escríbelo y lo agrego."]
    return "\n".join(lines)


def queued(position: int) -> str:
    return (
        f"Listo ✅, eres el pedido *#{position}* en la fila. "
        "La tienda te confirma el total en un momento."
    )


# ---- avisos de estado (los dispara el tendero desde el panel) ----

STATUS_PREPARANDO = "Estamos preparando tu pedido 🧑‍🍳"
STATUS_EN_CAMINO = "¡Tu pedido va en camino! 🛵"


def status_entregado() -> str:
    return f"Pedido entregado ✅ ¡Gracias por comprar en *{get_settings().store_name}*!"


def status_cancelado(reason: str | None) -> str:
    base = "Tu pedido fue cancelado ❌"
    return f"{base}. Motivo: {reason}" if reason else f"{base}."


def _cash_line(order: Order) -> str:
    change = order.change_due
    if change is None:
        return ""
    if change > 0:
        return f"\nPagas con {pesos(order.cash_amount)}: te llevamos {pesos(change)} de cambio 💵"
    if change == 0:
        return f"\nPagas con {pesos(order.cash_amount)}: justo, sin cambio 👌"
    return f"\nOjo: dijiste que pagas con {pesos(order.cash_amount)}, te faltarían {pesos(-change)} 🙏"


def total_confirmed(order: Order) -> str:
    return f"Tu pedido está confirmado ✅\nTotal: *{pesos(order.total)}*{_cash_line(order)}"


def total_corrected(order: Order) -> str:
    return f"Corrección: el total de tu pedido es *{pesos(order.total)}*{_cash_line(order)}"
