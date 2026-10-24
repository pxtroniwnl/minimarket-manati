"""Parsers tolerantes para dirección (torre/apto), montos en pesos y tipos de mensaje."""

import re
import unicodedata
from dataclasses import dataclass

from app.config import get_settings

MAX_AMOUNT = 2_000_000

_TORRE_KW = r"(?:torre|tor|tr|t|bloque|bl|edificio|edif|ed|interior|int)"
_APTO_KW = r"(?:apartamento|apto|apt|ap|a|casa|numero|nro|no|n|#)"

_TORRE = rf"\b{_TORRE_KW}\.?\s*(?P<torre>\d{{1,2}})(?!\d)"
_APTO = rf"(?:\b|(?<=\d)|(?<=#)|(?=#)){_APTO_KW}\.?\s*(?P<apto>\d{{3,4}})(?!\d)"

_RE_TORRE = re.compile(_TORRE)
_RE_APTO = re.compile(_APTO)
_RE_TORRE_THEN_NUM = re.compile(rf"{_TORRE}\s*[-/,.]?\s*(?P<apto>\d{{3,4}})(?!\d)")
_RE_BARE = re.compile(r"^(?P<torre>\d{1,2})\s*[-/ ]\s*(?P<apto>\d{3,4})$")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("°", "").replace("º", "")
    s = re.sub(r"[^\w#\s/.,$-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def words(text: str) -> str:
    """Texto normalizado sin puntuación en los extremos, para comparar con palabras clave."""
    return normalize(text).strip(" .,!¡?¿-")


@dataclass
class AddressResult:
    torre: int | None = None
    apto: str | None = None
    error: str | None = None  # motivo legible si la dirección no existe en el conjunto

    @property
    def ok(self) -> bool:
        return self.torre is not None and self.error is None

    @property
    def pair(self) -> tuple[int, str]:
        return self.torre, self.apto


def validate_address(torre: int, apto: str) -> str | None:
    """Valida contra la estructura real del conjunto. Devuelve el error o None."""
    s = get_settings()
    floors_by_tower = s.floors_by_tower
    if torre not in floors_by_tower:
        return f"En el conjunto hay torres de la {min(floors_by_tower)} a la {max(floors_by_tower)}."
    floors = floors_by_tower[torre]
    floor, unit = int(apto[:-2]), int(apto[-2:])
    last = f"{floors}{s.apts_per_floor:02d}"
    if not 1 <= floor <= floors:
        return f"La torre {torre} tiene {floors} pisos (apartamentos 101 a {last})."
    if not 1 <= unit <= s.apts_per_floor:
        return (
            f"Cada piso tiene los apartamentos 01 a {s.apts_per_floor:02d}, "
            f"por ejemplo {floor}01 a {floor}{s.apts_per_floor:02d}."
        )
    return None


def _result(torre: str, apto: str) -> AddressResult:
    t = int(torre)
    return AddressResult(t, apto, validate_address(t, apto))


def _candidates(s: str):
    # 1) torre y apto con palabra clave, en cualquier orden
    mt, ma = _RE_TORRE.search(s), _RE_APTO.search(s)
    if mt and ma and mt.span() != ma.span():
        yield mt["torre"], ma["apto"]
    # 2) torre con palabra clave seguida de número suelto ("t3 502", "t3-502")
    if m := _RE_TORRE_THEN_NUM.search(s):
        yield m["torre"], m["apto"]


def parse_address_result(text: str) -> AddressResult | None:
    """None si no parece una dirección; AddressResult con error si parece pero no existe."""
    s = normalize(text)
    if not s:
        return None
    first_invalid = None
    for torre, apto in _candidates(s):
        r = _result(torre, apto)
        if r.ok:
            return r
        first_invalid = first_invalid or r
    # 3) formato desnudo ("3-502", "3 502", "3/502")
    if m := _RE_BARE.match(s.strip(" .,")):
        r = _result(m["torre"], m["apto"])
        return r if r.ok else first_invalid or r
    return first_invalid


def parse_address(text: str) -> tuple[int, str] | None:
    r = parse_address_result(text)
    return r.pair if r and r.ok else None


def find_address_in(text: str) -> tuple[int, str] | None:
    """Dirección dentro de un texto más largo (p. ej. el pedido). Exige palabra clave de torre."""
    s = normalize(text)
    for torre, apto in _candidates(s):
        r = _result(torre, apto)
        if r.ok:
            return r.pair
    return None


_RE_PLACE = re.compile(
    r"^(?:en|pa|para|a|al|donde|hasta)\s+(?:la|el|mi|casa|donde|porteria|edificio|torre|apto|apartamento)\b"
    r"|\b(?:porteria|recepcion|torre|apartamento|apto|piso)\b"
)


def looks_like_address(text: str) -> bool:
    """¿El cliente intentó escribir una dirección? (para no agregarla al pedido por error)."""
    s = normalize(text)
    if _RE_PLACE.search(s):
        return True
    return bool(re.search(r"\d", s)) and (
        bool(_RE_TORRE.search(s)) or bool(_RE_APTO.search(s)) or bool(re.fullmatch(r"[\d\s/-]+", s))
    )


_RE_AMOUNT_SUFFIX = re.compile(
    r"(?P<num>\d+(?:[.,]\d{1,3})?)\s*(?P<suf>mil|k|lucas?|barras?|millon(?:es)?|palos?)\b"
)
_RE_THOUSANDS = re.compile(r"\b\d{1,3}(?:[.,]\d{3})+\b")
_RE_PLAIN = re.compile(r"\b\d+\b")


def parse_amount(text: str) -> int | None:
    """Monto en pesos. "50" se interpreta como 50 mil (números < 1000 sin sufijo)."""
    s = normalize(text).replace("$", " ")
    s = re.sub(r"\b(pesos|con|billete de|billete|de)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    value = None
    if m := _RE_AMOUNT_SUFFIX.search(s):
        factor = 1_000_000 if m["suf"].startswith(("millon", "palo")) else 1000
        value = round(float(m["num"].replace(",", ".")) * factor)
    elif m := _RE_THOUSANDS.search(s):
        value = int(re.sub(r"[.,]", "", m.group()))
    elif m := _RE_PLAIN.search(s):
        value = int(m.group())
        value = value * 1000 if value < 1000 else value
    if value is None or not 0 < value <= MAX_AMOUNT:
        return None
    return value


def format_address_short(torre: int, apto: str) -> str:
    return f"T{torre}-{apto}"


# ---- clasificación de mensajes cortos ----

_GREETING = (
    # Las alternativas largas van primero para que "buenas tardes" no quede en "buenas".
    r"(?:buenos dias|buenas tardes|buenas noches|buen dia|hola+|ola|holi|buenas|"
    r"que mas|q mas|quiubo|hey|saludos|alo|vecin[oa]s?|don|dona|senor[a]?|amig[oa]|"
    r"como esta[ns]?|como va|todo bien|bien y (?:usted|tu)|"
    r"disculpe|disculpa|una pregunta|buen(?:as|os)?)"
)
_RE_GREETING_PREFIX = re.compile(rf"^(?:{_GREETING}\b[\s,.!¡?¿-]*)+")
_COURTESY = {
    "gracias", "muchas gracias", "mil gracias", "ok", "okay", "oki", "listo", "dale", "perfecto",
    "bueno", "vale", "gracias vecino", "gracias vecina", "ok gracias", "listo gracias", "bendiciones",
    "chevere", "super", "genial", "de una",
}
_EMOJI_YES = {"👍", "👌", "🙏", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "🙌", "✅", "❤️", "😊"}
_RE_QUESTION_START = re.compile(
    r"^(?:tienen|tiene|hay|venden|vende|manejan|cuanto|cuanto vale|cuanto cuesta|a como|"
    r"que precio|precio|hasta que hora|abren|estan abiertos|hacen domicilio|llevan)\b"
)


def strip_greeting(text: str) -> str:
    """Quita saludos y muletillas del inicio conservando el texto original del resto."""
    s = text.strip()
    norm = normalize(s)
    m = _RE_GREETING_PREFIX.match(norm)
    if not m:
        return s
    # Avanza en el texto original tantas "palabras" como tenía el prefijo normalizado.
    n_words = len(m.group().split())
    parts = s.split()
    rest = " ".join(parts[n_words:])
    return rest.lstrip(" ,.!¡?¿-").strip()


def is_emoji_only(text: str) -> bool:
    t = text.strip()
    return bool(t) and not re.search(r"\w", t)


def is_greeting_only(text: str) -> bool:
    return not strip_greeting(text) or is_emoji_only(text)


def is_courtesy(text: str) -> bool:
    t = text.strip()
    return words(t) in _COURTESY or (bool(t) and all(ch in "".join(_EMOJI_YES) + " ️" for ch in t))


def is_yes_emoji(text: str) -> bool:
    return text.strip().replace("️", "") in {e.replace("️", "") for e in _EMOJI_YES}


def looks_like_question(text: str) -> bool:
    t = text.strip()
    return t.endswith("?") or t.startswith("¿") or bool(_RE_QUESTION_START.match(words(strip_greeting(t))))


def normalize_phone(phone: str) -> str | None:
    """'300 111 2233' / '+57 300-111-2233' → '+573001112233'. None si no parece un número."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:  # celular colombiano sin indicativo
        digits = "57" + digits
    return "+" + digits if 11 <= len(digits) <= 15 else None
