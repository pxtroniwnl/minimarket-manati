# Minimarket Manatí — pedidos por WhatsApp

Bot de pedidos por WhatsApp para la tienda del conjunto **Manatí** (Parque Heredia, Cartagena), con un panel web para el tendero.

- El residente escribe al WhatsApp de la tienda. El bot le pide el pedido, confirma la dirección (torre y apartamento) y le pregunta cómo paga. Cuando confirma, le da su turno en la fila.
- El tendero ve la fila en el celular (`/panel`), pone el total y va marcando cada pedido: preparando, en camino, entregado. Cuando hace falta, le escribe al cliente desde el mismo panel.

Todo está pensado para costar **$0**: el bot es determinista, sin IA de pago, con hosting y base de datos gratis y la API oficial de WhatsApp de Meta. Hay un detalle con los mensajes de Meta que conviene leer en [Costos](#costos).

---

## Índice
1. [Probarlo en tu computador (simulador)](#1-probarlo-en-tu-computador-simulador)
2. [Conectar WhatsApp real (Meta)](#2-conectar-whatsapp-real-meta) · [Prueba rápida con Twilio](#2b-prueba-rápida-con-el-sandbox-de-twilio-sin-número-propio)
3. [Publicarlo gratis: Render + Neon + UptimeRobot](#3-publicarlo-gratis-render--neon--uptimerobot)
4. [Alternativa: un PC en la tienda + Cloudflare Tunnel](#4-alternativa-un-pc-en-la-tienda--cloudflare-tunnel)
5. [Costos](#costos)
6. [Variables de entorno](#variables-de-entorno)
7. [Cómo funciona por dentro](#cómo-funciona-por-dentro)
8. [Mantenimiento](#mantenimiento)

---

## 1. Probarlo en tu computador (simulador)

En el modo `mock` no se conecta a WhatsApp. En `/simulador` hay un chat que imita el celular del residente, y en `/panel` lo ves llegar como lo vería el tendero.

### Con uv (recomendado)
Requisitos: Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env              # y cambia PANEL_PASSWORD y SECRET_KEY
uv sync
uv run alembic upgrade head       # crea dev.db (SQLite)
uv run python -m scripts.seed     # opcional: 3 pedidos de ejemplo
uv run uvicorn app.main:app --reload
```

- Simulador: http://localhost:8000/simulador. Abre dos pestañas con números distintos para simular dos residentes.
- Panel: http://localhost:8000/panel, con la contraseña `PANEL_PASSWORD` de tu `.env`.
- Tests: `uv run pytest -q`. Para correrlos contra Postgres: `TEST_DATABASE_URL=postgres://usuario@localhost:5432/una_db_vacia uv run pytest -q`.

### Con Docker
```bash
docker compose up --build
```
Levanta Postgres y la app en modo simulador en http://localhost:8000.

> Probado con Docker 29.1.3 y Compose 2.40: la imagen construye, aplica las migraciones al arrancar y un pedido completo por el simulador queda en la fila del panel. Los tests corren con SQLite y con Postgres 18.

### Contraseña del panel
Está en `.env` (`PANEL_PASSWORD`). Para cambiarla, edita el archivo y reinicia. Para generar valores seguros:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(16))"   # contraseña
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # SECRET_KEY
```
Si cambias `SECRET_KEY`, se cierran todas las sesiones del panel.

---

## 2. Conectar WhatsApp real (Meta)

Se usa **solo la API oficial de WhatsApp Cloud de Meta**. Nada de whatsapp-web.js, Baileys ni librerías no oficiales, que pueden hacer que bloqueen el número.

El bot **solo responde** dentro de la ventana de 24 horas que se abre cuando el residente escribe. Esos son *mensajes de servicio*. **Nunca** inicia conversaciones ni usa plantillas, porque eso siempre cuesta.

### 2.1 Crear la app
1. Entra a https://developers.facebook.com/ con la cuenta de Facebook de la tienda y crea una **app** de tipo *Business*.
2. En la app, agrega el producto **WhatsApp**. Meta te crea (o te pide) un *portafolio comercial* y te da un **número de prueba**.
3. En *WhatsApp → Configuración de la API* (API Setup) copia:
   - **Phone number ID** → `WA_PHONE_NUMBER_ID`. Ojo: es el *ID* del número, no el número.
   - El **token temporal**, que dura unas 24 h y sirve para la primera prueba → `WA_ACCESS_TOKEN`.
4. En *Configuración de la app → Básica* copia la **clave secreta de la app** (App Secret) → `WA_APP_SECRET`. Con ella se verifica que los webhooks vengan de verdad de Meta.
5. Inventa un texto largo cualquiera para `WA_VERIFY_TOKEN`. Lo vas a pegar también en Meta.
6. Con el número de prueba solo puedes escribirle a los números que agregues como destinatarios en esa misma pantalla. Agrega el tuyo.

### 2.2 Exponer tu computador para probar el webhook
Meta necesita una URL pública con HTTPS. Para probar desde tu computador:
```bash
# Terminal 1: la app en modo cloud (necesita Postgres, ver la sección 3, o docker compose)
WHATSAPP_MODE=cloud uv run uvicorn app.main:app --port 8000
# Terminal 2: túnel gratis y sin cuenta (la URL cambia cada vez que lo abres)
cloudflared tunnel --url http://localhost:8000
```
Te da una URL tipo `https://algo-raro.trycloudflare.com`.

> En modo `cloud` la app **no arranca** si falta alguna variable `WA_*`, si la contraseña del panel es la de ejemplo, si `SECRET_KEY` tiene menos de 16 caracteres o si la base es SQLite. Así no queda en producción algo mal configurado.

### 2.3 Configurar el webhook en Meta
1. Ve a *WhatsApp → Configuración* (Configuration) → **Webhook** → Editar.
   - **URL de devolución de llamada**: `https://TU-URL/webhook`
   - **Token de verificación**: el mismo `WA_VERIFY_TOKEN`
2. Toca *Verificar y guardar*. Si sale error, revisa que la app esté corriendo y que el token sea idéntico.
3. En los campos del webhook, **suscríbete a `messages`**.
4. Escríbele "hola" al número de prueba desde tu WhatsApp. El bot debe responder y el pedido debe aparecer en `/panel`.

### 2.4 Token permanente (para que no se desconecte)
El token temporal vence en horas. Para producción:
1. Entra a https://business.facebook.com/settings → *Usuarios → Usuarios del sistema* y crea uno con rol **Administrador**.
2. *Asignar activos*: dale tu app, con control total, y tu cuenta de WhatsApp.
3. *Generar token*: elige tu app, la expiración **Nunca** y los permisos `whatsapp_business_messaging` y `whatsapp_business_management`.
4. Pon ese token en `WA_ACCESS_TOKEN` y reinicia.

Si el token deja de servir, el panel muestra un **banner rojo "WhatsApp desconectado"**.

### 2.5 Pasar al número real de la tienda
En *WhatsApp → Configuración de la API → Agregar número de teléfono*, registra el número de la tienda y verifícalo por SMS o llamada. Ese número **no puede estar en uso al mismo tiempo en la app de WhatsApp del celular**. Si ya lo usan ahí, Meta indica cómo migrarlo; si no, usa un número nuevo. Después cambia `WA_PHONE_NUMBER_ID` por el ID del número nuevo.

---

## 2b. Prueba rápida con el Sandbox de Twilio (sin número propio)

Si todavía no tienes un número para Meta, puedes probar el bot en tu WhatsApp con el **Sandbox de Twilio**. Sirve **solo para probar**:
- La cuenta de prueba trae unos **100 mensajes gratis**, y Twilio cuenta tanto los entrantes como los salientes. Alcanzan para unos 8 a 10 pedidos de prueba. Después cobra cada mensaje.
- Todos usan el número compartido **+1 415 523 8886**. Cada persona que quiera probar le escribe primero `join <código>`, y esa conexión **vence a los 3 días**.
- Las opciones salen numeradas ("1️⃣ Efectivo, 2️⃣ Nequi…") y se responde con el número, porque el Sandbox no garantiza botones.

Pasos:
1. Crea una cuenta gratis en https://www.twilio.com/try-twilio.
2. En la consola entra a **Messaging → Try it out → Send a WhatsApp message**. Desde tu WhatsApp, envía el `join <código>` que aparece ahí al +1 415 523 8886.
3. En **Sandbox settings**, pon en *When a message comes in* `https://TU-SERVICIO.onrender.com/twilio/webhook` con método **POST**, y guarda.
4. En Render → *Environment*:
   - pega `TWILIO_ACCOUNT_SID` y `TWILIO_AUTH_TOKEN` (están en la portada de la consola de Twilio);
   - cambia `WHATSAPP_MODE=twilio`;
   - pon `WA_FREE_TIER=50` para que el contador del panel avise antes de que se acaben los mensajes de prueba.
5. Escribe "hola" al número del Sandbox.

La firma `X-Twilio-Signature` se valida con la URL pública. Render la entrega sola (`RENDER_EXTERNAL_URL`); en otro hosting, define `PUBLIC_URL`.

Cuando tengas el número de Meta, cambia `WHATSAPP_MODE=cloud` y listo: el bot, el panel y los datos son los mismos.

---

## 3. Publicarlo gratis: Render + Neon + UptimeRobot

### 3.1 Base de datos en Neon
1. Crea una cuenta en https://neon.com y un proyecto. Elige la región más cercana, por ejemplo `us-east`.
2. Copia la **connection string**: `postgresql://usuario:clave@ep-xxxx.../neondb?sslmode=require`. Esa es tu `DATABASE_URL`. La app la convierte sola a `postgresql+psycopg://`, así que puedes pegarla tal cual.

¿Por qué no la base gratis de Render? Porque **expira a los 30 días**. La de Neon no expira. Supabase también sirve: usa su cadena de conexión *Session pooler* (puerto 5432).

### 3.2 La app en Render
Render despliega desde un repositorio de Git, así que primero sube este proyecto a GitHub (en un repositorio **privado**). `.env` está en `.gitignore`: **nunca lo subas**.

1. En https://render.com → *New → Web Service* → conecta el repositorio.
2. *Language*: **Docker**. *Instance type*: **Free**.
3. *Health Check Path*: `/health`.
4. *Environment*: agrega las variables de [Variables de entorno](#variables-de-entorno), al menos `DATABASE_URL`, `WHATSAPP_MODE=cloud`, las cuatro `WA_*`, `PANEL_PASSWORD` y `SECRET_KEY`.
5. Despliega. El contenedor corre `alembic upgrade head` solo al arrancar.
6. En Meta, cambia la URL del webhook a `https://TU-SERVICIO.onrender.com/webhook`.

### 3.3 Que no se duerma: UptimeRobot
El plan gratis de Render **apaga el servicio después de 15 minutos sin tráfico** y despertarlo tarda alrededor de un minuto. Para evitarlo:
1. Crea una cuenta en https://uptimerobot.com.
2. Crea un monitor **HTTP(s)** a `https://TU-SERVICIO.onrender.com/health` cada **5 minutos**.

Aunque se llegue a dormir, no se pierden pedidos: si Meta no recibe respuesta, reintenta el webhook durante horas, y los mensajes que quedaron a medias se reprocesan al arrancar.

> Render da **750 horas gratis al mes por espacio de trabajo**, y un mes tiene unas 730. Alcanza para **un solo** servicio despierto todo el mes. No crees otro servicio gratis en el mismo workspace.

---

## 4. Alternativa: un PC en la tienda + Cloudflare Tunnel

Si en la tienda hay un PC prendido todo el día:
```bash
cp .env.example .env    # WHATSAPP_MODE=cloud y los datos de Meta
docker compose up -d --build
cloudflared tunnel --url http://localhost:8000
```

- Ventaja: no se duerme y la base es local (no hay límite de 0.5 GB).
- Desventajas: si se va la luz o el internet, el bot se cae (Meta reintenta por un tiempo). Además, la URL del túnel rápido **cambia cada vez que lo reinicias**, y hay que actualizarla en Meta. Para tener una URL fija hace falta un *túnel con nombre* de Cloudflare, que es gratis pero requiere un dominio en Cloudflare, y el dominio sí cuesta.

---

## Costos

| Servicio | Para qué | Plan gratis (verificado en octubre de 2026) | ¿Cuesta? |
|---|---|---|---|
| **WhatsApp Cloud API (Meta)** | Recibir y responder mensajes | Recibir es gratis. Las respuestas dentro de las 24 h (*mensajes de servicio*) son gratis **hasta 1.000 entregados al mes por número**, desde el 1 de octubre de 2026 | ⚠️ **Puede costar** si pasas de 1.000 al mes (ver abajo) |
| **Render** | Correr la app | 750 h/mes por workspace. Se duerme a los 15 min sin tráfico. Disco efímero | $0 |
| **Neon** | Base de datos Postgres | 0.5 GB por proyecto, 100 CU-horas/mes, se suspende a los 5 min sin uso | $0 |
| **UptimeRobot** | Que Render no se duerma | 50 monitores, chequeo cada 5 min | $0 |
| **Cloudflare Tunnel** | URL pública desde un PC (alternativa) | Túnel rápido gratis y sin cuenta | $0 (el dominio para una URL fija sí cuesta) |
| **Gemini** (opcional, apagado) | Entender pedidos escritos raro | Nivel gratis de Google AI Studio, con límites por minuto y por día | $0 si no lo activas |

### ⚠️ Los 1.000 mensajes de Meta
Antes todas las respuestas dentro de las 24 h eran gratis e ilimitadas. **Desde el 1 de octubre de 2026, Meta solo regala los primeros 1.000 mensajes de servicio entregados al mes por número** y cobra los siguientes.

**Si no hay un método de pago en la cuenta, Meta puede dejar de entregar los mensajes que pasen del límite.** En la práctica, el bot se queda mudo a fin de mes.

Lo que hace el proyecto:
- **Ahorra mensajes.** Un pedido completo cuesta 7 mensajes o menos para un cliente nuevo y 6 o menos para uno que repite: la dirección se confirma junto con el pago, las notas son opcionales y los avisos de "preparando" y "entregado" vienen apagados. Eso da para **unos 140 a 200 pedidos al mes gratis**.
- **Cuenta los mensajes.** El panel muestra "📨 N / 1.000 mensajes gratis este mes" con una barra. Se pone amarillo al 80% y rojo al 100%. Es una cota superior: cuenta los que Meta aceptó, no solo los entregados.
- **Recomendación: agrega un método de pago** en el portafolio comercial (https://business.facebook.com → *Facturación y pagos* → cuenta de WhatsApp → *Agregar método de pago*). Mientras no pases de 1.000 no se cobra nada, y si pasas, el bot sigue funcionando. El precio por mensaje depende del país: revisa la tarifa de **Colombia** en la tabla oficial de precios de Meta.

### Fuentes (los planes gratis cambian: revísalos antes de lanzar)
- Meta, precios de WhatsApp: https://developers.facebook.com/docs/whatsapp/pricing
- Render, plan gratis: https://render.com/docs/free
- Neon, precios: https://neon.com/pricing
- UptimeRobot, preguntas frecuentes: https://help.uptimerobot.com/
- Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- Gemini API: https://ai.google.dev/gemini-api/docs/pricing

---

## Variables de entorno

Todas están en `.env.example` con comentarios.

| Variable | Por defecto | Qué hace |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dev.db` | Base de datos. En producción, Postgres. Se aceptan URLs `postgres://`, `postgresql://` o `postgresql+psycopg://` |
| `WHATSAPP_MODE` | `mock` | `mock` (simulador), `cloud` (Meta real) o `twilio` (Sandbox, solo pruebas) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | — | Credenciales de Twilio (ver la sección 2b) |
| `TWILIO_FROM` | `whatsapp:+14155238886` | Número del Sandbox |
| `PUBLIC_URL` | `RENDER_EXTERNAL_URL` | URL pública, usada para validar la firma de Twilio |
| `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET` | — | Datos de Meta (ver la sección 2) |
| `WA_GRAPH_VERSION` | `v26.0` | Versión de la Graph API |
| `WA_FREE_TIER` | `1000` | Mensajes gratis al mes, usados por el contador del panel |
| `NOTIFY_PREPARING` / `NOTIFY_DELIVERED` | `false` | Avisar al cliente "estamos preparando" o "entregado". Cada aviso es un mensaje más. "En camino" siempre se envía |
| `PANEL_PASSWORD` | `cambiame` | Contraseña del panel. En modo cloud es obligatorio cambiarla |
| `SECRET_KEY` | `dev-secret-cambiame` | Firma la cookie del panel (16 caracteres o más) |
| `STORE_NAME` | `Minimarket Manatí` | Nombre que aparece en los mensajes y en el panel |
| `OPEN_HOUR` / `CLOSE_HOUR` | `7` / `22` | Horario. Fuera de él, el bot avisa que la tienda está cerrada |
| `TZ` | `America/Bogota` | Zona horaria |
| `TOWERS_LAYOUT` | `1-3:7,4-10:11` | Torres y pisos del conjunto. Con esto se validan las direcciones |
| `APTS_PER_FLOOR` | `4` | Apartamentos por piso (x01 a x04) |
| `DRAFT_TTL_MINUTES` | `120` | Un pedido sin terminar por más de este tiempo se descarta |
| `COURTESY_WINDOW_MINUTES` | `180` | Durante este tiempo después de un pedido, un "gracias" recibe "¡Con gusto!" y no abre un pedido nuevo |
| `RATE_LIMIT_PER_MINUTE` | `15` | Si un número manda más mensajes por minuto, se guardan pero no se responden |
| `RETENTION_DAYS` | `90` | Los mensajes más viejos se borran |
| `LLM_PROVIDER` | `none` | `gemini` para activar la IA opcional. Si falla, se usan las reglas |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | —, `gemini-3.8-flash` | Clave de Google AI Studio |

---

## Cómo funciona por dentro

```
WhatsApp ──► POST /webhook ──► se verifica la firma ──► se guarda el mensaje ──► 200 a Meta
                                                              │
                                                     (en segundo plano)
                                                              ▼
                                   bot (flujo guiado, sin IA) ──► respuesta por la Cloud API
                                                              │
                                                              ▼
                                            panel del tendero (actualización en vivo por SSE)
```

- **Estados del pedido**: `borrador` → `en_cola` (aquí recibe el turno) → `confirmado` (el tendero puso el total) → `preparando` → `en_camino` → `entregado`. Puede pasar a `cancelado` desde cualquiera de ellos.
- **Turno**: lo da la hora de confirmación del cliente. Si dos confirman en el mismo instante, se desempata por el número del pedido.
- **El bot solo atiende el borrador.** Cuando el pedido entra a la fila, lo que escriba el cliente va al panel y responde el tendero.
- **Catálogo de escenarios**: `docs/escenarios.md` lista más de 70 casos (errores, correcciones, mensajes partidos, botones viejos, reinicios…). Cada uno tiene su test.
- **Código**:
  - `app/flow/`: el bot y la máquina de estados.
  - `app/services/`: parsers, cola, entrada y salida de mensajes, Meta.
  - `app/routers/`: webhook, panel y simulador.
  - `app/templates/`: el panel (Jinja2 y HTMX).

### ⚠️ Un solo proceso (1 worker)
La app corre con **un solo proceso** (`--workers 1` en el `Dockerfile`). Los candados que ordenan los mensajes de cada cliente y las notificaciones en vivo del panel viven en memoria. Con dos procesos, dos mensajes del mismo cliente podrían procesarse a la vez y el panel no se enteraría de lo que pasa en el otro proceso. Para una tienda sobra: cada mensaje se procesa en milisegundos. **No subas el número de workers ni las instancias.**

---

## Mantenimiento

- **Mensajes viejos**: para no pasar los 0.5 GB de Neon, cada vez que la app arranca borra los mensajes de más de `RETENTION_DAYS` días. Los pedidos y el historial se conservan, y no se tocan los mensajes de pedidos activos. En Render la app arranca seguido, así que no hay que hacer nada. En un PC que nunca se reinicia, corre de vez en cuando:
  ```bash
  uv run python -m scripts.purge          # o: docker compose exec app python -m scripts.purge
  ```
- **Datos de ejemplo**: `uv run python -m scripts.seed` carga 3 pedidos. Solo funciona en modo mock y con la base vacía.
- **Migraciones**: `uv run alembic upgrade head`. El contenedor las corre solo al arrancar.
- **Privacidad (Ley 1581)**: la primera vez que alguien escribe, el bot le dice que su número y su dirección solo se usan para sus pedidos. En los logs los teléfonos salen enmascarados (`+57300***2233`) y nunca se registran tokens.
- **Pedidos por llamada o en persona**: con "➕ Nuevo pedido" en el panel el tendero carga el pedido y este entra a la fila igual que los de WhatsApp. Al cliente no se le envía nada (iniciar conversaciones cuesta); si deja su celular y luego escribe, su chat queda unido al pedido.
- **Política de privacidad**: es pública en `/privacidad` (Ley 1581). Meta pide esa URL para publicar la app, y la sección `/privacidad#borrar` sirve como instrucciones para borrar los datos.
- **Pausar pedidos**: el botón "⏸️ Pausar pedidos" del panel hace que el bot conteste que por ahora no se reciben pedidos. Sirve, por ejemplo, cuando el tendero sale un momento.
