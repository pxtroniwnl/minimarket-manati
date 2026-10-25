# Escenarios de calidad — Bot de pedidos Manatí

Criterios de aceptación del sistema. Cada escenario tiene un ID que aparece en el código (`[A4]`, `[D1]`…)
y en los tests (`tests/test_scenarios.py::test_A4_…`).

Leyenda: ✅ implementado y con test · 📋3 / 📋4 / 📋5 pendiente para esa fase
(3 = panel, 4 = integración con Meta, 5 = despliegue).

Estructura del conjunto (validación de direcciones): 10 torres; torres 1–3 con 7 pisos y torres 4–10 con 11 pisos;
4 apartamentos por piso (x01–x04). Configurable con `TOWERS_LAYOUT` y `APTS_PER_FLOOR`.

## Catálogo

### A. Toma del pedido
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| A1 | "hola" como primer mensaje | Saludo y Paso 1. Se crea el borrador sin turno | ✅ |
| A2 | Primer mensaje con saludo y pedido ("hola vecino, me regala 2 panes y una leche") | Se quita el saludo y el resto queda como `order_text`. El bot responde "Anoté: …" y pasa a la dirección | ✅ |
| A3 | Primer mensaje que también trae la dirección ("2 panes pa la t3 502") | Se toma el pedido y se precarga la dirección. El bot pregunta [Sí, T3-502] [Otra dirección] | ✅ |
| A4 | Pedido partido en varios mensajes ("2 panes" → "y una leche" → "ah y hielo") en los pasos de dirección, pago o resumen | Si el texto no es una dirección, ni una opción, ni una palabra clave, se agrega al pedido ("Agregué *y una leche* 👍") y se repite la pregunta del paso | ✅ |
| A5 | En el Paso 1 manda solo un saludo, "?", un emoji o "ok" | No se guarda como pedido. Se repite el Paso 1 con un ejemplo | ✅ |
| A6 | Pregunta sobre productos ("¿tienen pan integral?") en el Paso 1 | Se detecta como pregunta (empieza por tienen/hay/venden/cuánto o termina en "?") y se ofrecen [Es mi pedido] [Preguntar a tienda] | ✅ |
| A7 | Pedido muy largo (más de 1000 caracteres) | Se guarda completo. En el resumen con botones se recorta a unos 600 caracteres con "…", porque el body de un mensaje con botones admite máximo 1024 | ✅ |
| A8 | Imagen de una lista, audio, sticker o ubicación durante el borrador | "Por ahora solo puedo leer texto 🙏" y queda registrado | ✅ |

### B. Dirección (Manatí)
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| B1 | Formatos tolerantes ("t3 502", "3-502", "torre 3 apto 502"…) | Se parsean | ✅ |
| B2 | Torre inexistente ("torre 12 apto 502") | Error específico: "En Manatí hay torres de la 1 a la 10" | ✅ |
| B3 | Piso inexistente ("t2 905", "t5 1205") | "La torre 2 tiene 7 pisos (apartamentos 101 a 704)" | ✅ |
| B4 | Apartamento inexistente en el piso ("t3 507") | "Cada piso tiene los apartamentos 01 a 04, por ejemplo 501–504" | ✅ |
| B5 | Dirección guardada | Botones [Sí, T3-502] [Otra dirección] | ✅ |
| B6 | Responde "sí", "dale" o "👍" en vez de tocar el botón | Se acepta | ✅ |
| B7 | Tres intentos de dirección fallidos | Se ofrece [Hablar con la tienda] además del ejemplo, para que no quede en un bucle infinito | ✅ |
| B8 | Pide para otro apartamento (un vecino o un familiar) | "Otra dirección". La última dirección usada queda guardada | ✅ |

### C. Pago
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| C1 | Botón o texto ("por nequi", "efectivo") | Se acepta | ✅ |
| C2 | Monto en palabras ("veinte mil") | Se pide en números | ✅ |
| C3 | Monto absurdo ("500000000") o cero | Se rechaza, con un tope de $2.000.000 | ✅ |
| C4 | Cambia de efectivo a Nequi corrigiendo | Se borra `cash_amount` | ✅ |

### D. Resumen y corrección
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| D1 | Corregir | Muestra [🛒 Pedido] [📍 Dirección] [💳 Pago]. Cambia solo lo elegido y vuelve al resumen | ✅ |
| D2 | Escribe algo en el resumen ("ah y un hielo") | Se agrega al pedido y se reenvía el resumen actualizado. El resumen dice "¿Te faltó algo? Escríbelo y lo agrego" | ✅ |
| D3 | Corrige el pago a efectivo | Pide el monto y vuelve al resumen, sin pasar por las notas | ✅ |
| D4 | Toca un botón viejo (del paso anterior o de un pedido anterior) | Se ignora y se repite la pregunta del paso actual. Los ids de botón llevan el id del pedido (`pago_nequi:123`) | ✅ |
| D5 | Toca "✅ Confirmar" dos veces | La segunda vez no hace nada, no avisa al panel y no marca no leído | ✅ |
| D6 | "reiniciar" o "cancelar" en cualquier paso del borrador | Funciona | ✅ |

### E. Después de confirmar (cola y traspaso)
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| E1 | Escribe estando en `en_cola` o un estado posterior | El mensaje va al panel y se marca no leído. El bot solo responde el aviso de E10 | ✅ |
| E2 | Quiere agregar algo después de confirmar | Va al panel. El tendero puede editar el pedido | ✅ |
| E3 | "cancelar" en `en_cola` | Se cancela y se le confirma al cliente | ✅ |
| E4 | "cancelar" en confirmado, preparando o en camino | No se cancela: queda `cancel_requested` y el bot responde "Le avisé a la tienda, ellos te confirman" para que no quede sin respuesta | ✅ |
| E5 | Dos clientes confirman en el mismo instante | Posiciones distintas: el empate se desempata por id. Hoy ambos quedarían como #1 | ✅ |
| E6 | Su pedido pasa a ser el #1 (el de adelante se entregó o se canceló) | Aviso "¡Eres el siguiente! 🎉" una sola vez (`next_notified`), solo si está en en_cola o confirmado | ✅ |
| E7 | Si queda de #1 justo al confirmar | No se manda aviso extra: el mensaje de confirmación ya dice #1 | ✅ |
| E8 | Reacciones (👍 sobre un mensaje), pines, encuestas y otros eventos de sistema | Se ignoran sin responder. No crean borrador ni marcan no leído | ✅ |
| E9 | El cliente edita un mensaje (WhatsApp lo manda como `unsupported/edit`, sin el texto nuevo) | En borrador: "Vi que editaste un mensaje… ¿me lo escribes de nuevo?". En cola: queda en el chat del panel como "[mensaje editado — no se ve la edición]". Sin pedido: se ignora | ✅ |
| E10 | Escribe durante el traspaso y nadie le responde (p. ej. "hola" con el pedido en camino) | Una sola vez: "📩 Le pasé tu mensaje a la tienda…". No se repite mientras siga siendo lo último enviado, y no sale si el tendero le escribió hace menos de 30 min (`HANDOFF_CHAT_MINUTES`) | ✅ |

### F. Sin pedido activo o con un borrador viejo
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| F1 | "cancelar" o "reiniciar" sin pedido activo | "No tienes pedidos en curso 🙂", sin crear borrador. Hoy crea uno: bug | ✅ |
| F2 | "gracias", "ok" o 👍 hasta 3 horas después de que se entregó o canceló su pedido | "¡Con gusto! 🙌", sin crear borrador | ✅ |
| F3 | Borrador abandonado más de 2 horas que se retoma | Se descarta y empieza de cero: "Tu pedido anterior quedó sin terminar, empecemos de nuevo". El TTL es configurable con `DRAFT_TTL_MINUTES` | ✅ |
| F4 | Fuera de horario sin borrador | Mensaje de tienda cerrada, sin pedido | ✅ |
| F5 | La tienda pausa los pedidos manualmente (el tendero sale) | Desde el panel. El bot responde con el mensaje de cerrado | ✅ |

### G. Hablar con la tienda
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| G1 | Escribe "tienda", "ayuda", "asesor", "hablar con alguien" o toca [Preguntar a tienda] durante el borrador | Se marca `needs_help`, el bot deja de responder en ese borrador y contesta "Le paso tu mensaje a la tienda 🙋" | ✅ |
| G2 | Borrador con `needs_help` en el panel | Sale en una sección "Consultas", separada de la fila y sin turno | ✅ |
| G3 | El tendero resuelve la consulta | Botones "Devolver al bot" (se reanuda el paso actual y se reenvía la pregunta) o "Cancelar" | ✅ |

### H. Robustez e infraestructura
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| H1 | Meta reintenta el mismo webhook | Se deduplica por `wa_message_id` | ✅ |
| H2 | Excepción inesperada en el bot | El mensaje entrante se guarda primero, en su propia transacción. La lógica corre aparte: si falla, se hace rollback solo de esa parte, se registra el error y al cliente le llega "Uy, tuvimos un problema 😓 intenta de nuevo en un momento" | ✅ |
| H3 | El servidor se reinicia a mitad de un proceso (deploy en Render) | Los mensajes entrantes con `processed_at` nulo se reprocesan al arrancar | ✅ |
| H4 | La DB no responde (Neon despertando) | El webhook devuelve 500 para que Meta reintente después. Se guarda antes de responder, con `pool_pre_ping` | ✅ |
| H5 | Falla el envío a Meta (red o 5xx) | Dos reintentos con backoff. Si sigue fallando, `send_error` se ve en el panel | ✅ |
| H6 | Token de Meta vencido o inválido (código 190) | Banner rojo en el panel "WhatsApp desconectado". El README explica cómo usar un token permanente de System User | ✅ |
| H7 | Envío fuera de la ventana de 24h | No se envía y el panel muestra "El cliente debe escribir primero…" | ✅ |
| H8 | Mensaje de más de 4096 caracteres o body con botones de más de 1024 | `outbox` parte el texto o lo manda aparte de los botones | ✅ |
| H9 | Spam: más de 15 mensajes por minuto del mismo número | Se registran pero el bot no responde a los que se pasen | ✅ |
| H11 | La app arranca en modo `cloud` con secretos por defecto, sin datos de Meta o con SQLite | No arranca y dice qué falta | ✅ |
| H10 | Dos procesos de servidor (escalar) | No soportado: el Dockerfile fija `--workers 1` y el README lo explica (los candados y el SSE viven en memoria) | ✅ |

### I. Panel del tendero
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| I1 | Escribe el total como "12.500", "12500" o "12 mil" | Se reutiliza `parse_amount`. Se muestra el valor interpretado antes de confirmar | ✅ |
| I2 | El efectivo del cliente no alcanza para el total | Aviso en rojo al tendero. Igual puede confirmar, y al cliente le dice "el total es $X, te faltarían $Y" | ✅ |
| I3 | Se equivocó en el total ya confirmado | Puede corregirlo. Se le reenvía el total al cliente ("Corrección: …") | ✅ |
| I4 | El cliente pide agregar o cambiar algo | El tendero edita pedido, dirección, pago y notas. Queda una nota en el chat | ✅ |
| I5 | Toca Entregado o Cancelar por error | Diálogo de confirmación (`hx-confirm`). Cancelar tiene motivos rápidos ("No tenemos lo que pediste", "Vamos a cerrar", "Otro") que se le envían al cliente | ✅ |
| I6 | Dos celulares con el panel abierto tocan el mismo botón | La segunda transición falla con `InvalidTransition`. Se muestra "Este pedido ya cambió de estado" y se recarga, nunca un error 500 | ✅ |
| I7 | Pedido esperando mucho sin atender | La tarjeta se pone amarilla a los 15 min y roja a los 30. Los pedidos de días anteriores se marcan "de ayer" | ✅ |
| I8 | Se cae la conexión del celular | SSE se reconecta solo, con polling cada 3s de respaldo. Los botones se desactivan mientras se envía (`hx-disabled-elt`) | ✅ |
| I9 | Fuerza bruta a la contraseña | 5 intentos por minuto por IP. Cookie HttpOnly, Secure y SameSite=Lax, con sesión de 30 días | ✅ |
| I10 | Pedido con `cancel_requested` | Se resalta "El cliente pidió cancelar" con [Cancelar pedido] [Seguir] | ✅ |
### J. Datos, privacidad y costo
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| J1 | Ley 1581 (habeas data) | El primer saludo, solo la primera vez, incluye una línea corta: "Usamos tu número y dirección solo para tus pedidos" | ✅ |
| J2 | Límite de almacenamiento del tier gratis (Neon 0.5 GB) | Al arrancar se borran los mensajes de más de `RETENTION_DAYS` (90) días que no son de pedidos activos ni están pendientes | ✅ |
| J3 | Texto del cliente con HTML o scripts (XSS) en el panel | Autoescape de Jinja y el filtro `wa` escapa antes de dar formato | ✅ |
| J4 | Tokens o datos personales en los logs | No se loguean tokens. Los teléfonos se enmascaran en el log (+57300***2233) | ✅ |

### K. Ahorro de mensajes (Meta cobra después de 1.000 al mes)
Desde el 1 de octubre de 2026, Meta cobra los mensajes de servicio después de los primeros 1.000 entregados al mes por número. Por eso el flujo se recortó.

| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| K1 | Dirección junto con el pago | No se pregunta la dirección aparte. El mensaje del pago dice "📍 Te lo llevamos a *Torre X Apto Y*. Si es otra dirección, escríbela." Si el cliente escribe otra dirección se cambia, y si no es válida se le explica el error | ✅ |
| K2 | Notas opcionales | No se preguntan. El resumen tiene el botón [🗒️ Agregar nota] | ✅ |
| K3 | Avisos de "preparando" y "entregado" | Apagados por defecto (`NOTIFY_PREPARING`, `NOTIFY_DELIVERED`). "En camino" siempre se envía | ✅ |
| K4 | Contador del mes en el panel | Cuenta los salientes aceptados este mes. Amarillo al 80% de `WA_FREE_TIER`, rojo al 100%. No cuenta los fallidos ni los de meses anteriores | ✅ |
| K5 | Presupuesto por pedido | Cliente nuevo: 7 mensajes o menos por pedido completo. Cliente que repite: 6 o menos | ✅ (`test_K_message_budget_*`) |

### L. Pedidos cargados por la tienda (llamada o en persona)
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| L1 | El tendero toma un pedido por teléfono o en el mostrador | "➕ Nuevo pedido" en el panel: pedido, dirección y pago obligatorios; nombre, celular, notas y total opcionales. Entra directo a la fila (o queda confirmado si ya trae el total) y se marca con 🏪 | ✅ |
| L2 | Datos mal escritos | Mismos errores que el bot (torre/piso/apto inexistente, efectivo sin monto, celular inválido) sin perder lo escrito | ✅ |
| L3 | Cliente sin WhatsApp | Nunca se llama a Meta: los avisos quedan en el chat como "sin WhatsApp" y el panel lo advierte | ✅ |
| L4 | Deja su celular y luego escribe por WhatsApp | El mensaje se une a ese pedido (va al panel, no abre otro borrador) y su dirección queda guardada | ✅ |
| L5 | El cliente ya tiene un pedido en curso | Se rechaza y se le indica al tendero que edite el existente | ✅ |

### M. Prueba con el Sandbox de Twilio (`WHATSAPP_MODE=twilio`)
| ID | Escenario | Comportamiento esperado | Estado |
|---|---|---|---|
| M1 | Webhook sin firma, con firma alterada o sin token configurado | 403 y no se guarda nada. La firma se calcula sobre la URL pública y coincide con el `RequestValidator` oficial | ✅ |
| M2 | Texto, botón, imagen, audio, documento o ubicación | Se traducen al mismo `IncomingMessage` que produce Meta | ✅ |
| M3 | El cliente responde "2" a un mensaje con opciones | Equivale a tocar el 2.º botón. Un número sin opciones previas se queda como texto. Un `MessageSid` repetido se ignora | ✅ |
| M4 | Twilio avisa `undelivered`/`failed` (StatusCallback) | El mensaje queda con el error en el panel (p. ej. "fuera de las 24 h") | ✅ |
| M5 | Envío | Opciones numeradas, partición a 1.600 caracteres, reintento en 5xx, 63016 → fuera de ventana, 63015 → no se unió al Sandbox, 20003 → banner de credenciales | ✅ |
| M6 | Número de prueba de la consola nueva (rechaza texto libre por API: error 21654) | Las respuestas del bot viajan como TwiML en la respuesta del webhook, escapadas y contadas en el uso mensual. Lo dirigido a otro número (aviso "eres el siguiente") y lo del panel va por la API | ✅ |

