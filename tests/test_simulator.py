import re


def _tap(client, phone, html, kind):
    """Toca el botón dibujado en el chat (su id lleva el id del pedido)."""
    button_id = re.findall(rf"{kind}:\d+", html)[-1]
    return client.post("/simulador/send", data={"phone": phone, "kind": "button", "button_id": button_id, "text": kind})


def test_simulator_requires_login(client):
    assert client.get("/simulador", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/simulador/send", data={"phone": "+573001110001", "text": "hola"}, follow_redirects=False).status_code == 303


def test_simulator_full_order(panel):
    client = panel
    phone = "+573001110001"
    assert client.get("/simulador").status_code == 200
    r = client.post("/simulador/send", data={"phone": phone, "text": "hola", "name": "Luis"})
    assert "¡Hola, Luis!" in r.text
    client.post("/simulador/send", data={"phone": phone, "text": "1 bolsa de hielo"})
    r = client.post("/simulador/send", data={"phone": phone, "text": "torre 2 apto 304"})
    r = _tap(client, phone, r.text, "pago_transf")
    assert "resumen_ok:" in r.text  # el botón se dibuja con su id
    r = _tap(client, phone, r.text, "resumen_ok")
    assert "<strong>#1</strong> en la fila" in r.text


def test_simulator_accepts_local_number(panel):
    client = panel
    client.post("/simulador/send", data={"phone": "300 111 0009", "text": "hola"})
    r = client.get("/simulador/chat", params={"phone": "+573001110009"})
    assert "¿Qué quieres pedir?" in r.text


def test_simulator_audio(panel):
    client = panel
    phone = "+573001110002"
    client.post("/simulador/send", data={"phone": phone, "text": "hola"})
    r = client.post("/simulador/send", data={"phone": phone, "kind": "audio"})
    assert "[audio recibido]" in r.text and "solo puedo leer" in r.text


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_privacy_policy_is_public(client):
    r = client.get("/privacidad")
    assert r.status_code == 200 and "Ley 1581" in r.text and 'id="borrar"' in r.text
