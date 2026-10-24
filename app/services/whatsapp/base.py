from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Button:
    id: str
    title: str  # Meta: máximo 20 caracteres


@dataclass
class OutgoingMessage:
    text: str
    buttons: list[Button] = field(default_factory=list)


@dataclass
class IncomingMessage:
    phone: str  # E.164 con "+"
    type: str  # text | button | audio | image | sticker | location | video | document | unknown
    text: str = ""
    button_id: str | None = None
    wa_message_id: str | None = None
    name: str | None = None


@dataclass
class SendResult:
    ok: bool
    wa_message_id: str | None = None
    error: str | None = None
    # True cuando Meta rechaza porque el cliente no ha escrito en las últimas 24h.
    outside_window: bool = False


class WhatsAppClient(Protocol):
    def send(self, to: str, message: OutgoingMessage) -> SendResult: ...
