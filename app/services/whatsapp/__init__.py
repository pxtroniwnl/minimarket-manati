from functools import lru_cache

from app.config import get_settings
from app.services.whatsapp.base import WhatsAppClient


@lru_cache
def get_whatsapp_client() -> WhatsAppClient:
    if get_settings().whatsapp_mode == "cloud":
        from app.services.whatsapp.cloud import CloudWhatsAppClient

        return CloudWhatsAppClient.from_settings()
    if get_settings().whatsapp_mode == "twilio":
        from app.services.whatsapp.twilio import TwilioWhatsAppClient

        return TwilioWhatsAppClient.from_settings()
    from app.services.whatsapp.mock import MockWhatsAppClient

    return MockWhatsAppClient()
