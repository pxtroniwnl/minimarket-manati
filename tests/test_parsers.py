import pytest

from app.services.parsers import (
    find_address_in,
    is_courtesy,
    is_greeting_only,
    looks_like_address,
    looks_like_question,
    parse_address,
    parse_address_result,
    parse_amount,
    strip_greeting,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("t3 502", (3, "502")),
        ("T3-502", (3, "502")),
        ("torre 3 apto 502", (3, "502")),
        ("torre 3 apartamento 502", (3, "502")),
        ("Torre 3, Apto 502", (3, "502")),
        ("3-502", (3, "502")),
        ("3/502", (3, "502")),
        ("3 502", (3, "502")),
        ("tr3 ap502", (3, "502")),
        ("apto 502 torre 3", (3, "502")),
        ("bloque 3 # 502", (3, "502")),
        ("TORRE 10 APTO 1104", (10, "1104")),
        ("t 3 a 502", (3, "502")),
        ("torre3apto502", (3, "502")),
        ("Tórre 7 Apartamento 1103", (7, "1103")),
        ("en la torre 5 apto 301 porfa", (5, "301")),
        ("T.4 Apt. 202", (4, "202")),
        ("torre 3 - 502", (3, "502")),
    ],
)
def test_parse_address_ok(text, expected):
    assert parse_address(text) == expected


@pytest.mark.parametrize(
    "text",
    ["hola", "502", "torre 99 apto 502", "torre 3", "pa la 4, el 302", "", "t3 50"],
)
def test_parse_address_invalid(text):
    assert parse_address(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("20 mil", 20_000),
        ("20mil", 20_000),
        ("20k", 20_000),
        ("20K", 20_000),
        ("20.000", 20_000),
        ("20,000", 20_000),
        ("20000", 20_000),
        ("$20.000", 20_000),
        ("50", 50_000),
        ("con 50", 50_000),
        ("100 mil", 100_000),
        ("con un billete de 50 mil", 50_000),
        ("20.5 mil", 20_500),
        ("100.000 pesos", 100_000),
        ("2 lucas", 2_000),
    ],
)
def test_parse_amount_ok(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["veinte mil", "veinte", "no sé", "", "0", "500000000", "3 millones"])
def test_parse_amount_invalid(text):
    assert parse_amount(text) is None


@pytest.mark.parametrize(
    "text,ok",
    [
        ("t1 101", True),
        ("t3 704", True),
        ("t3 705", False),  # solo hay 4 aptos por piso
        ("t3 801", False),  # torre 3 tiene 7 pisos
        ("t4 1104", True),
        ("t4 1201", False),  # torre 4 tiene 11 pisos
        ("t10 1101", True),
        ("t11 101", False),  # solo hay 10 torres
        ("t0 101", False),
        ("t5 100", False),  # el apto 00 no existe
        ("t2 104", True),
        ("t9 805", False),
    ],
)
def test_manati_layout(text, ok):
    assert (parse_address(text) is not None) is ok


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("torre 12 apto 502", "torres de la 1 a la 10"),
        ("t2 905", "La torre 2 tiene 7 pisos (apartamentos 101 a 704)"),
        ("t5 1205", "La torre 5 tiene 11 pisos (apartamentos 101 a 1104)"),
        ("t3 507", "apartamentos 01 a 04, por ejemplo 501 a 504"),
        ("3-507", "por ejemplo 501 a 504"),
    ],
)
def test_specific_address_errors(text, fragment):
    r = parse_address_result(text)
    assert r is not None and not r.ok
    assert fragment in r.error


def test_not_an_address_returns_none():
    assert parse_address_result("y una leche") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hola vecino, me regala 2 panes y una leche", "me regala 2 panes y una leche"),
        ("Buenas tardes! 2 panes", "2 panes"),
        ("hola", ""),
        ("Buenos días vecina 🙂", "🙂"),
        ("2 panes", "2 panes"),
        ("holaa buenas, una gaseosa", "una gaseosa"),
    ],
)
def test_strip_greeting(text, expected):
    assert strip_greeting(text) == expected


@pytest.mark.parametrize("text", ["hola", "buenas", "Buenos días vecina", "👋", "🙂🙂", "hola!!"])
def test_greeting_only(text):
    assert is_greeting_only(text)


@pytest.mark.parametrize("text", ["2 panes", "hola, un hielo"])
def test_not_greeting_only(text):
    assert not is_greeting_only(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2 panes pa la t3 502", (3, "502")),
        ("una leche, torre 4 apto 1101 porfa", (4, "1101")),
        ("2 panes 1 leche 500g", None),
        ("1 arroz de 500 y 2 huevos", None),
        ("3 cervezas 330ml", None),
    ],
)
def test_find_address_in(text, expected):
    assert find_address_in(text) == expected


@pytest.mark.parametrize("text", ["t3", "torre 3 apto", "3-50", "apto 502", "502", "en la casa de mi mamá", "pa la porteria", "a la torre del frente"])
def test_looks_like_address(text):
    assert looks_like_address(text)


@pytest.mark.parametrize("text", ["y una leche", "ah y hielo", "2 panes más"])
def test_not_looks_like_address(text):
    assert not looks_like_address(text)


@pytest.mark.parametrize("text", ["¿tienen pan integral?", "tienen pan integral", "hola, hay huevos?", "cuánto vale el hielo"])
def test_question(text):
    assert looks_like_question(text)


@pytest.mark.parametrize("text", ["2 panes y una leche", "hola, un hielo"])
def test_not_question(text):
    assert not looks_like_question(text)


@pytest.mark.parametrize("text", ["gracias", "Muchas gracias!", "ok", "👍", "🙏🙏", "listo gracias"])
def test_courtesy(text):
    assert is_courtesy(text)


@pytest.mark.parametrize("text", ["hola", "2 panes", "gracias, ahora quiero un hielo"])
def test_not_courtesy(text):
    assert not is_courtesy(text)
