"""Flujo guiado y determinista del bot. Recibe un mensaje y devuelve las respuestas.

Los IDs entre corchetes ([A4], [D1]…) remiten al catálogo de escenarios en docs/escenarios.md.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.flow import texts
from app.flow.machine import transition
from app.flow.states import ACTIVE_STATUSES, HUMAN_STATUSES, FlowStep, OrderStatus, PaymentMethod
from app.models import Customer, Message, Order
from app.services import hours, parsers, store
from app.services.extractor import Extractor
from app.services.parsers import words
from app.services.queue import queue_position
from app.services.whatsapp.base import Button, IncomingMessage, OutgoingMessage
from app.utils import as_utc

# Tipos de botón. El id que viaja a WhatsApp es "<tipo>:<id del pedido>" [D4].
DIR_SI, DIR_OTRA = "dir_si", "dir_otra"
PAGO_EFECTIVO, PAGO_NEQUI, PAGO_TRANSF = "pago_efectivo", "pago_nequi", "pago_transf"
NOTAS_NO = "notas_no"
RESUMEN_OK, RESUMEN_EDIT, RESUMEN_NOTA = "resumen_ok", "resumen_edit", "resumen_nota"
EDIT_PEDIDO, EDIT_DIR, EDIT_PAGO = "edit_pedido", "edit_dir", "edit_pago"
ES_PEDIDO, PREGUNTAR = "es_pedido", "preguntar"
AYUDA = "ayuda"

STALE = object()  # botón de otro pedido o de un mensaje viejo

# Tipos de mensaje que se ignoran sin responder [E8].
IGNORED_TYPES = {"reaction", "system", "unsupported", "ephemeral", "request_welcome", "order"}

_YES = {"si", "s", "sip", "ok", "okay", "dale", "listo", "esa", "correcto", "confirmar", "confirmo", "va", "de una", "si senor", "si senora"}
_NO_NOTES = {"no", "nada", "ninguna", "ninguno", "no nada mas", "nada mas", "no gracias", "n", "asi esta bien"}
_CANCEL = {"cancelar", "cancela", "cancelar pedido", "cancelo", "cancelen", "cancelalo", "ya no", "ya no quiero"}
_RESTART = {"reiniciar", "reinicia", "reiniciar pedido", "empezar de nuevo", "volver a empezar"}
_HELP = {"tienda", "ayuda", "asesor", "humano", "persona", "hablar con alguien", "hablar con la tienda", "necesito ayuda", "atencion"}
_OTHER_ADDRESS = {"no", "otra", "otra direccion", "otro", "otro apto", "otro apartamento"}

MAX_ADDRESS_ATTEMPTS = 3

NON_TEXT_LABEL = {
    "audio": "[audio recibido]",
    "image": "[imagen recibida]",
    "sticker": "[sticker recibido]",
    "location": "[ubicación recibida]",
    "video": "[video recibido]",
    "document": "[documento recibido]",
    "contacts": "[contacto recibido]",
    "reaction": "[reacción]",
    "edit": "[mensaje editado — no se ve la edición]",
    "unknown": "[mensaje no soportado]",
}


@dataclass
class BotResult:
    order: Order | None
    replies: list[OutgoingMessage] = field(default_factory=list)
    new_order_queued: bool = False
    human_message: bool = False  # el mensaje es para el tendero (panel)
    status_changed: bool = False


def is_cancel(text: str) -> bool:
    return words(text) in _CANCEL


def get_active_order(db: Session, customer: Customer) -> Order | None:
    return db.scalar(
        select(Order).where(
            Order.customer_id == customer.id,
            Order.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
    )


def _last_closed_order(db: Session, customer: Customer) -> Order | None:
    return db.scalar(
        select(Order)
        .where(
            Order.customer_id == customer.id,
            Order.status.in_([OrderStatus.ENTREGADO.value, OrderStatus.CANCELADO.value]),
        )
        .order_by(Order.updated_at.desc())
        .limit(1)
    )


def _msg(text: str, buttons: list[Button] | None = None) -> OutgoingMessage:
    return OutgoingMessage(text=text, buttons=buttons or [])


def _btn(kind: str, order: Order, title: str) -> Button:
    return Button(f"{kind}:{order.id}", title)


def _looks_like_items(text: str) -> bool:
    """¿Es un pedazo más del pedido? (para los pasos donde no se espera texto libre) [A4]."""
    w = words(text)
    if not w or parsers.is_courtesy(text) or parsers.is_greeting_only(text) or parsers.looks_like_address(text):
        return False
    if w in _YES or w in _NO_NOTES or w in _CANCEL or w in _RESTART or w in _HELP:
        return False
    return len(w.split()) >= 2 or w.startswith(("y ", "ah ", "+", "tambien"))


def _new_draft(db: Session, customer: Customer) -> Order:
    order = Order(customer_id=customer.id)
    db.add(order)
    db.flush()
    return order


class Bot:
    def __init__(self, extractor: Extractor):
        self.extractor = extractor

    # ---------------------------------------------------------------- entrada

    def handle(
        self, db: Session, customer: Customer, inbound: Message, msg: IncomingMessage, now: datetime
    ) -> BotResult:
        order = get_active_order(db, customer)

        if msg.type in IGNORED_TYPES:  # [E8]
            inbound.order_id = order.id if order else None
            return BotResult(order)

        # Traspaso a humano: desde en_cola el bot no responde [E1].
        if order and order.status in [s.value for s in HUMAN_STATUSES]:
            return self._handoff(db, order, inbound, msg, now)

        prefix = ""
        if order and self._expired(order, now):  # [F3]
            transition(order, OrderStatus.CANCELADO)
            db.flush()
            order, prefix = None, texts.EXPIRED

        if order is None:
            return self._no_active_order(db, customer, inbound, msg, now, prefix)

        inbound.order_id = order.id

        if order.needs_help:  # [G1] atiende la tienda
            order.has_unread = True
            return BotResult(order, human_message=True)

        if msg.type == "edit":  # WhatsApp no nos manda el texto editado
            return BotResult(order, [_msg(texts.EDITED)])
        if msg.type not in ("text", "button"):
            return BotResult(order, [_msg(texts.ONLY_TEXT)])

        button = self._button(msg, order)
        if button is STALE:  # [D4]
            return BotResult(order, [self._prompt(customer, order)])

        if msg.type == "text":
            w = words(msg.text)
            if w in _RESTART:
                transition(order, OrderStatus.CANCELADO)
                db.flush()
                order = _new_draft(db, customer)
                inbound.order_id = order.id
                return BotResult(order, [_msg(f"{texts.RESTARTED}\n\n{texts.ASK_ORDER}")])
            if w in _CANCEL:
                transition(order, OrderStatus.CANCELADO)
                return BotResult(order, [_msg(texts.CANCELLED)], status_changed=True)
            if w in _HELP:
                return self._ask_for_help(order)
        elif button == AYUDA:
            return self._ask_for_help(order)

        handler = getattr(self, f"_step_{order.flow_step}")
        return handler(db, customer, order, msg, button)

    def _expired(self, order: Order, now: datetime) -> bool:
        ttl = timedelta(minutes=get_settings().draft_ttl_minutes)
        return order.status == OrderStatus.BORRADOR and not order.needs_help and now - as_utc(order.updated_at) > ttl

    def _button(self, msg: IncomingMessage, order: Order):
        if msg.type != "button" or not msg.button_id:
            return None
        kind, _, order_id = msg.button_id.partition(":")
        return kind if order_id == str(order.id) else STALE

    def _no_active_order(self, db, customer, inbound, msg, now, prefix) -> BotResult:
        if msg.type == "edit":  # editó un mensaje viejo: no abre un pedido nuevo
            return BotResult(None)
        if msg.type == "text":
            w = words(msg.text)
            if w in _CANCEL or w in _RESTART:  # [F1]
                return BotResult(None, [_msg(texts.NO_ACTIVE_ORDER)])
            if parsers.is_courtesy(msg.text) and not prefix:  # [F2]
                last = _last_closed_order(db, customer)
                window = timedelta(minutes=get_settings().courtesy_window_minutes)
                if last and now - as_utc(last.updated_at) <= window:
                    return BotResult(None, [_msg(texts.COURTESY_REPLY)])
        if msg.type == "button":
            # Botón de un pedido que ya terminó: no abre uno nuevo por accidente.
            return BotResult(None, [_msg(texts.NO_ACTIVE_ORDER)])
        if not hours.is_open(now):
            return BotResult(None, [_msg(texts.closed())])
        if store.is_paused(db):  # [F5]
            return BotResult(None, [_msg(texts.PAUSED)])

        order = _new_draft(db, customer)
        inbound.order_id = order.id
        return self._start(customer, order, msg, prefix)

    def _start(self, customer: Customer, order: Order, msg: IncomingMessage, prefix: str) -> BotResult:
        intro = [prefix] if prefix else [texts.greeting(customer.name)]
        if not customer.privacy_notified:  # [J1]
            intro.append(texts.PRIVACY)
            customer.privacy_notified = True
        head = "\n".join(intro)

        rest = parsers.strip_greeting(msg.text) if msg.type == "text" else ""
        if not rest or parsers.is_greeting_only(msg.text) or parsers.is_courtesy(rest):  # [A1]
            return BotResult(order, [_msg(f"{head}\n\n{texts.ASK_ORDER}")])
        if words(rest) in _HELP:
            result = self._ask_for_help(order)
            result.replies[0].text = f"{head}\n\n{result.replies[0].text}"
            return result
        if parsers.looks_like_question(msg.text):  # [A6]
            return self._ask_if_question(order, rest, head)
        # [A2] el primer mensaje ya trae el pedido
        return self._take_order_text(customer, order, rest, head=f"{head}\n\n{texts.noted(rest)}")

    # ------------------------------------------------------- traspaso a humano

    def _handoff(self, db: Session, order: Order, inbound: Message, msg: IncomingMessage, now: datetime) -> BotResult:
        inbound.order_id = order.id
        if msg.type == "button":
            # Botón viejo del bot (p. ej. doble toque en Confirmar): no molesta al tendero [D5].
            return BotResult(order)
        order.has_unread = True
        result = BotResult(order, human_message=True)
        if msg.type == "text" and is_cancel(msg.text):
            if order.status == OrderStatus.EN_COLA:  # [E3]
                transition(order, OrderStatus.CANCELADO)
                result.replies.append(_msg(texts.CANCELLED))
                result.status_changed = True
            else:  # [E4] ya está en proceso: decide el tendero
                order.cancel_requested = True
                result.replies.append(_msg(texts.CANCEL_REQUESTED))
        elif self._needs_handoff_ack(db, order, now):  # [E10]
            result.replies.append(_msg(texts.HANDOFF_ACK))
        return result

    def _needs_handoff_ack(self, db: Session, order: Order, now: datetime) -> bool:
        """Que el cliente no quede sin respuesta, pero sin repetir el aviso ni meterse si el tendero está chateando."""
        last_out = db.scalar(
            select(Message).where(Message.order_id == order.id, Message.direction == "out").order_by(Message.id.desc()).limit(1)
        )
        if last_out is None:
            return True
        if last_out.sender == "bot" and last_out.text == texts.HANDOFF_ACK:
            return False
        chatting = (last_out.payload_json or {}).get("kind") == "chat"
        window = timedelta(minutes=get_settings().handoff_chat_minutes)
        return not (chatting and now - as_utc(last_out.created_at) <= window)

    def _ask_for_help(self, order: Order) -> BotResult:  # [G1]
        order.needs_help = True
        order.has_unread = True
        return BotResult(order, [_msg(texts.HELP)], human_message=True)

    # ------------------------------------------------------- pasos del borrador

    def _step_pedido(self, db, customer, order, msg, button) -> BotResult:
        text = msg.text.strip() if msg.type == "text" else ""
        if not text or parsers.is_greeting_only(text) or parsers.is_courtesy(text):  # [A5]
            return BotResult(order, [_msg(texts.ASK_ORDER_AGAIN)])
        if order.edit_target == "pedido":
            order.order_text = text
            order.edit_target = None
            return self._go_summary(order)
        rest = parsers.strip_greeting(text)
        if parsers.looks_like_question(text):  # [A6]
            return self._ask_if_question(order, rest)
        return self._take_order_text(customer, order, rest)

    def _ask_if_question(self, order: Order, text: str, head: str = "") -> BotResult:
        order.order_text = text
        order.flow_step = FlowStep.PREGUNTA.value
        body = f"{head}\n\n{texts.IS_IT_AN_ORDER}" if head else texts.IS_IT_AN_ORDER
        return BotResult(
            order,
            [_msg(body, [_btn(ES_PEDIDO, order, "🛒 Es mi pedido"), _btn(PREGUNTAR, order, "🙋 Preguntar a tienda")])],
        )

    def _step_pregunta(self, db, customer, order, msg, button) -> BotResult:
        w = words(msg.text) if msg.type == "text" else ""
        if button == ES_PEDIDO or w in _YES | {"pedido", "es mi pedido", "es un pedido"}:
            return self._take_order_text(customer, order, order.order_text or "")
        if button == PREGUNTAR or w in {"pregunta", "preguntar", "es una pregunta"}:
            return self._ask_for_help(order)
        return BotResult(order, [self._prompt(customer, order)])

    def _take_order_text(self, customer: Customer, order: Order, text: str, head: str = "") -> BotResult:
        order.order_text = text
        if found := parsers.find_address_in(text):  # [A3]
            order.torre, order.apto = found
        elif customer.has_address:  # [B5]
            order.torre, order.apto = customer.torre, customer.apto
        else:
            order.flow_step = FlowStep.DIR_ESCRIBIR.value
            return BotResult(order, [_msg(self._join(head, texts.ASK_ADDRESS))])
        # Modo ahorro: la dirección va junto a la pregunta de pago; si es otra, la escribe [K1].
        return self._go_payment(order, head=head, show_address=True)

    def _candidate_address(self, customer: Customer, order: Order) -> tuple[int, str]:
        if order.torre is not None and order.apto:
            return order.torre, order.apto
        return customer.torre, customer.apto

    def _address_question(self, customer: Customer, order: Order, head: str = "") -> OutgoingMessage:
        torre, apto = self._candidate_address(customer, order)
        return _msg(
            self._join(head, texts.confirm_address(torre, apto)),
            [
                _btn(DIR_SI, order, f"Sí, {parsers.format_address_short(torre, apto)}"),
                _btn(DIR_OTRA, order, "Otra dirección"),
            ],
        )

    def _step_dir_confirmar(self, db, customer, order, msg, button) -> BotResult:
        w = words(msg.text) if msg.type == "text" else ""
        if button == DIR_SI or w in _YES or (msg.type == "text" and parsers.is_yes_emoji(msg.text)):  # [B6]
            order.torre, order.apto = self._candidate_address(customer, order)
            return self._after_address(order)
        if button == DIR_OTRA or w in _OTHER_ADDRESS:
            order.torre, order.apto = None, None
            order.flow_step = FlowStep.DIR_ESCRIBIR.value
            return BotResult(order, [_msg(texts.ASK_ADDRESS)])
        if msg.type == "text":
            result = parsers.parse_address_result(msg.text)
            if result and result.ok:  # escribió otra dirección directamente
                order.torre, order.apto = result.pair
                return self._after_address(order)
            if result:  # [B2–B4]
                order.flow_step = FlowStep.DIR_ESCRIBIR.value
                return self._address_failed(order, texts.address_invalid(result.error))
            if _looks_like_items(msg.text) or not parsers.looks_like_address(msg.text):  # [A4]
                return BotResult(order, [self._append(order, msg.text), self._address_question(customer, order)])
        return BotResult(order, [self._address_question(customer, order, texts.NOT_UNDERSTOOD_BUTTONS)])

    def _step_dir_escribir(self, db, customer, order, msg, button) -> BotResult:
        if msg.type != "text":
            return BotResult(order, [_msg(texts.ASK_ADDRESS)])
        result = parsers.parse_address_result(msg.text)
        if result and result.ok:
            order.torre, order.apto = result.pair
            return self._after_address(order)
        if result:  # [B2–B4] parece dirección pero no existe en el conjunto
            return self._address_failed(order, texts.address_invalid(result.error))
        if found := self.extractor.extract_address(msg.text):  # LLM opcional
            order.torre, order.apto = found
            return self._after_address(order)
        if parsers.looks_like_address(msg.text):
            return self._address_failed(order, texts.ADDRESS_RETRY)
        # [A4] no es una dirección: era otra parte del pedido
        return BotResult(order, [self._append(order, msg.text), _msg(texts.ASK_ADDRESS)])

    def _address_failed(self, order: Order, text: str) -> BotResult:
        order.address_attempts = (order.address_attempts or 0) + 1
        if order.address_attempts >= MAX_ADDRESS_ATTEMPTS:  # [B7]
            return BotResult(
                order,
                [_msg(f"{text}\n\n{texts.ADDRESS_HELP_OFFER}", [_btn(AYUDA, order, "Hablar con la tienda")])],
            )
        return BotResult(order, [_msg(text)])

    def _after_address(self, order: Order) -> BotResult:
        order.address_attempts = 0
        if order.edit_target == "direccion":
            order.edit_target = None
            return self._go_summary(order)
        return self._go_payment(order)

    def _step_pago(self, db, customer, order, msg, button) -> BotResult:
        method = {
            PAGO_EFECTIVO: PaymentMethod.EFECTIVO,
            PAGO_NEQUI: PaymentMethod.NEQUI,
            PAGO_TRANSF: PaymentMethod.TRANSFERENCIA,
        }.get(button or "")
        if method is None and msg.type == "text":
            w = words(msg.text)
            if w in _OTHER_ADDRESS - {"no"}:
                order.flow_step = FlowStep.DIR_ESCRIBIR.value
                return BotResult(order, [_msg(texts.ASK_ADDRESS)])
            address = parsers.parse_address_result(msg.text)
            if address and address.ok:  # [K1] "no, es la t4 1101"
                order.torre, order.apto = address.pair
                return self._go_payment(order, head=texts.address_changed(*address.pair))
            if address:  # [B2–B4]
                return BotResult(order, [_msg(texts.address_invalid(address.error)), self._prompt(customer, order)])
            if "efectivo" in w or "cash" in w:
                method = PaymentMethod.EFECTIVO
            elif "nequi" in w:
                method = PaymentMethod.NEQUI
            elif "transf" in w or "bancolombia" in w or "daviplata" in w:
                method = PaymentMethod.TRANSFERENCIA
            elif _looks_like_items(msg.text):  # [A4]
                return BotResult(order, [self._append(order, msg.text), self._prompt(customer, order)])
        if method is None:
            return BotResult(order, [_msg(f"{texts.NOT_UNDERSTOOD_BUTTONS}\n{texts.ASK_PAYMENT}", self._payment_buttons(order))])
        order.payment_method = method.value
        if method == PaymentMethod.EFECTIVO:
            order.flow_step = FlowStep.EFECTIVO_MONTO.value
            return BotResult(order, [_msg(texts.ASK_CASH)])
        order.cash_amount = None  # [C4]
        return self._after_payment(order)

    def _step_efectivo_monto(self, db, customer, order, msg, button) -> BotResult:
        if msg.type != "text":
            return BotResult(order, [_msg(texts.ASK_CASH)])
        amount = self.extractor.extract_amount(msg.text)
        if amount:
            order.cash_amount = amount
            return self._after_payment(order)
        if _looks_like_items(msg.text) and not any(c.isdigit() for c in msg.text) and "mil" not in words(msg.text):
            return BotResult(order, [self._append(order, msg.text), _msg(texts.ASK_CASH)])
        return BotResult(order, [_msg(texts.CASH_RETRY)])

    def _after_payment(self, order: Order) -> BotResult:
        # Sin paso de notas obligatorio: la nota es un botón opcional del resumen [K2].
        order.edit_target = None  # [D3]
        return self._go_summary(order)

    def _step_notas(self, db, customer, order, msg, button) -> BotResult:
        if button == NOTAS_NO or (msg.type == "text" and words(msg.text) in _NO_NOTES):
            order.notes = order.notes if order.edit_target == "nota" else None
            order.edit_target = None
        elif msg.type == "text" and msg.text.strip():
            order.notes = msg.text.strip()
            order.edit_target = None
        else:
            return BotResult(order, [self._prompt(customer, order)])
        return self._go_summary(order)

    def _step_resumen(self, db, customer, order, msg, button) -> BotResult:
        w = words(msg.text) if msg.type == "text" else ""
        if button == RESUMEN_OK or w in _YES or (msg.type == "text" and parsers.is_yes_emoji(msg.text)):
            return self._confirm(db, customer, order)
        if button == RESUMEN_EDIT or w in {"corregir", "editar", "cambiar", "no", "corregir pedido"}:
            return self._go_edit_menu(order)
        if button == RESUMEN_NOTA or w in {"nota", "agregar nota", "indicacion"}:
            order.flow_step = FlowStep.NOTAS.value
            order.edit_target = "nota"
            return BotResult(order, [_msg(texts.ASK_NOTE)])
        if msg.type == "text" and msg.text.strip() and not parsers.is_courtesy(msg.text) and not parsers.is_greeting_only(msg.text):
            # [D2] "ah, y un hielo" → se agrega y se reenvía el resumen
            ack = self._append(order, msg.text)
            summary = self._go_summary(order).replies[0]
            return BotResult(order, [ack, summary])
        return BotResult(order, [self._prompt(customer, order)])

    def _step_edit_menu(self, db, customer, order, msg, button) -> BotResult:  # [D1]
        w = words(msg.text) if msg.type == "text" else ""
        if button == EDIT_PEDIDO or w in {"pedido", "el pedido", "productos"}:
            order.edit_target = "pedido"
            order.flow_step = FlowStep.PEDIDO.value
            return BotResult(order, [_msg(texts.ASK_ORDER_EDIT)])
        if button == EDIT_DIR or w in {"direccion", "la direccion", "apto", "torre"}:
            order.edit_target = "direccion"
            order.flow_step = FlowStep.DIR_ESCRIBIR.value
            return BotResult(order, [_msg(texts.ASK_ADDRESS)])
        if button == EDIT_PAGO or w in {"pago", "el pago", "forma de pago"}:
            order.edit_target = "pago"
            order.flow_step = FlowStep.PAGO.value
            return BotResult(order, [_msg(texts.ASK_PAYMENT, self._payment_buttons(order))])
        return BotResult(order, [self._prompt(customer, order)])

    # --------------------------------------------------------- transiciones

    def _confirm(self, db: Session, customer: Customer, order: Order) -> BotResult:
        transition(order, OrderStatus.EN_COLA)
        customer.torre, customer.apto = order.torre, order.apto
        db.flush()
        position = queue_position(db, order)
        if position == 1:  # [E7] ya sabe que es el primero
            order.next_notified = True
        return BotResult(order, [_msg(texts.queued(position))], new_order_queued=True, status_changed=True)

    def _go_payment(self, order: Order, head: str = "", show_address: bool = False) -> BotResult:
        order.flow_step = FlowStep.PAGO.value
        return BotResult(order, [self._payment_message(order, head, show_address)])

    def _payment_message(self, order: Order, head: str = "", show_address: bool = False) -> OutgoingMessage:
        text = texts.ASK_PAYMENT
        if show_address:
            text = f"{texts.deliver_to(order.torre, order.apto)}\n\n{text}"
        return _msg(self._join(head, text), self._payment_buttons(order))

    def _go_summary(self, order: Order) -> BotResult:
        order.flow_step = FlowStep.RESUMEN.value
        return BotResult(order, [self._summary_message(order)])

    def _go_edit_menu(self, order: Order) -> BotResult:
        order.flow_step = FlowStep.EDIT_MENU.value
        return BotResult(order, [self._edit_menu_message(order)])

    # ------------------------------------------------------------- helpers

    def _append(self, order: Order, text: str) -> OutgoingMessage:
        extra = text.strip()
        order.order_text = f"{order.order_text} + {extra}" if order.order_text else extra
        return _msg(texts.appended(extra))

    def _payment_buttons(self, order: Order) -> list[Button]:
        return [
            _btn(PAGO_EFECTIVO, order, "Efectivo"),
            _btn(PAGO_NEQUI, order, "Nequi"),
            _btn(PAGO_TRANSF, order, "Transferencia"),
        ]

    def _summary_message(self, order: Order) -> OutgoingMessage:
        return _msg(
            texts.summary(order),
            [
                _btn(RESUMEN_OK, order, "✅ Confirmar"),
                _btn(RESUMEN_EDIT, order, "✏️ Corregir"),
                _btn(RESUMEN_NOTA, order, "🗒️ Agregar nota"),
            ],
        )

    def _edit_menu_message(self, order: Order) -> OutgoingMessage:
        return _msg(
            texts.EDIT_MENU,
            [_btn(EDIT_PEDIDO, order, "🛒 Pedido"), _btn(EDIT_DIR, order, "📍 Dirección"), _btn(EDIT_PAGO, order, "💳 Pago")],
        )

    def _prompt(self, customer: Customer, order: Order) -> OutgoingMessage:
        """La pregunta del paso actual, para repetirla cuando no se entiende la respuesta."""
        step = FlowStep(order.flow_step)
        if step == FlowStep.PEDIDO:
            return _msg(texts.ASK_ORDER_EDIT if order.edit_target else texts.ASK_ORDER)
        if step == FlowStep.PREGUNTA:
            return self._ask_if_question(order, order.order_text or "").replies[0]
        if step == FlowStep.DIR_CONFIRMAR:
            return self._address_question(customer, order)
        if step == FlowStep.DIR_ESCRIBIR:
            return _msg(texts.ASK_ADDRESS)
        if step == FlowStep.PAGO:
            return self._payment_message(order, show_address=order.torre is not None and not order.edit_target)
        if step == FlowStep.EFECTIVO_MONTO:
            return _msg(texts.ASK_CASH)
        if step == FlowStep.NOTAS:
            return _msg(texts.ASK_NOTE)
        if step == FlowStep.EDIT_MENU:
            return self._edit_menu_message(order)
        return self._summary_message(order)

    @staticmethod
    def _join(head: str, text: str) -> str:
        return f"{head}\n\n{text}" if head else text
