import uuid

from app.services.whatsapp.base import OutgoingMessage, SendResult


class MockWhatsAppClient:
    """No envía nada: el mensaje ya queda guardado en la DB por outbox y el simulador lo lee."""

    def __init__(self):
        self.sent: list[tuple[str, OutgoingMessage]] = []

    def send(self, to: str, message: OutgoingMessage) -> SendResult:
        self.sent.append((to, message))
        return SendResult(ok=True, wa_message_id=f"mock-{uuid.uuid4().hex}")
