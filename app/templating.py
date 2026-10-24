from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.utils import local, pesos

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["pesos"] = pesos
templates.env.filters["hora"] = lambda dt: local(dt).strftime("%-I:%M %p").lower() if dt else ""
templates.env.globals["store_name"] = get_settings().store_name


def _wa_health():
    from app.services.whatsapp.cloud import health

    return health


templates.env.globals["wa_health"] = _wa_health


def wa_format(text: str):
    """Formato de WhatsApp (*negrita*, _cursiva_) a HTML, escapando primero."""
    import re

    from markupsafe import Markup, escape

    s = str(escape(text or ""))
    s = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<em>\1</em>", s)
    return Markup(s.replace("\n", "<br>"))


templates.env.filters["wa"] = wa_format
