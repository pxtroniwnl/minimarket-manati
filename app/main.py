import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import panel, simulator, twilio_webhook, webhook
from app.services.auth import NotLoggedIn
from app.startup import RedactSecrets, validate_settings
from app.templating import templates

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("uvicorn.access").addFilter(RedactSecrets())
log = logging.getLogger(__name__)


def _startup_jobs() -> None:
    from app.db import SessionLocal
    from app.services.inbox import reprocess_pending
    from app.services.retention import purge_old_messages
    from app.services.whatsapp import get_whatsapp_client

    try:
        with SessionLocal() as db:
            if n := reprocess_pending(db, get_whatsapp_client()):
                log.info("Reprocesados %s mensajes pendientes", n)
            # El hosting gratis reinicia seguido, así que limpiar al arrancar basta [J2].
            purge_old_messages(db)
    except Exception:
        log.exception("Falló una tarea de arranque")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings(get_settings())
    # Mensajes que quedaron a medias por un reinicio [H3]; sin bloquear el arranque.
    task = asyncio.create_task(asyncio.to_thread(_startup_jobs))
    yield
    task.cancel()


app = FastAPI(title=get_settings().store_name, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(webhook.router)
# Siempre montado: sin TWILIO_AUTH_TOKEN rechaza todo (firma inválida).
app.include_router(twilio_webhook.router)
app.include_router(panel.router)
app.add_exception_handler(NotLoggedIn, panel.not_logged_in_handler)
app.add_exception_handler(panel.OrderActionError, panel.order_error_handler)

if get_settings().whatsapp_mode == "mock":
    app.include_router(simulator.router)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/privacidad", response_class=HTMLResponse)
def privacy(request: Request):
    """Política de datos (Ley 1581). Meta la exige para publicar la app; es pública."""
    return templates.TemplateResponse(
        request, "privacidad.html", {"updated": "24 de octubre de 2026", "retention_days": get_settings().retention_days}
    )


@app.get("/")
def root():
    return RedirectResponse("/simulador" if get_settings().whatsapp_mode == "mock" else "/panel")
