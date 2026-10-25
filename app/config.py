from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"

    # mock = simulador · cloud = Meta Cloud API · twilio = Sandbox de Twilio (solo para pruebas)
    whatsapp_mode: Literal["mock", "cloud", "twilio"] = "mock"
    wa_phone_number_id: str = ""
    wa_access_token: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_graph_version: str = "v26.0"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = "whatsapp:+14155238886"  # número compartido del Sandbox
    # URL pública del servicio (firma de Twilio y avisos de entrega). Render la da en RENDER_EXTERNAL_URL.
    public_url: str = Field("", validation_alias=AliasChoices("PUBLIC_URL", "RENDER_EXTERNAL_URL"))

    llm_provider: Literal["none", "gemini"] = "none"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    panel_password: str = "cambiame"
    secret_key: str = "dev-secret-cambiame"

    store_name: str = "Minimarket Manatí"
    open_hour: int = 7
    close_hour: int = 22
    tz: str = "America/Bogota"

    # Estructura del conjunto Manatí: torres 1–3 con 7 pisos, 4–10 con 11; 4 aptos por piso.
    # Formato "torres:pisos" separado por comas.
    towers_layout: str = "1-3:7,4-10:11"
    apts_per_floor: int = 4

    # Meta cobra los mensajes de servicio después de 1.000 entregados por número al mes (desde 2026-10-01).
    wa_free_tier: int = 1000
    # Avisos opcionales: cada uno es un mensaje más por pedido. "Confirmado" y "en camino" siempre van.
    notify_preparing: bool = False
    notify_delivered: bool = False

    # Mensajes más viejos que esto se borran para no llenar los 0,5 GB gratis de Neon [J2].
    retention_days: int = 90

    draft_ttl_minutes: int = 120
    courtesy_window_minutes: int = 180
    # Si el tendero escribió hace menos de esto, el bot no interrumpe con el aviso de traspaso [E10].
    handoff_chat_minutes: int = 30
    rate_limit_per_minute: int = 15

    @field_validator("database_url")
    @classmethod
    def _psycopg3(cls, url: str) -> str:
        # Neon/Render/Supabase entregan "postgres://…"; usamos el driver psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def floors_by_tower(self) -> dict[int, int]:
        result = {}
        for part in self.towers_layout.split(","):
            towers, floors = part.split(":")
            lo, _, hi = towers.partition("-")
            for t in range(int(lo), int(hi or lo) + 1):
                result[t] = int(floors)
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
