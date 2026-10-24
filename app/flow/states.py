from enum import StrEnum


class OrderStatus(StrEnum):
    BORRADOR = "borrador"
    EN_COLA = "en_cola"
    CONFIRMADO = "confirmado"
    PREPARANDO = "preparando"
    EN_CAMINO = "en_camino"
    ENTREGADO = "entregado"
    CANCELADO = "cancelado"


class FlowStep(StrEnum):
    PEDIDO = "pedido"
    DIR_CONFIRMAR = "dir_confirmar"
    DIR_ESCRIBIR = "dir_escribir"
    PAGO = "pago"
    EFECTIVO_MONTO = "efectivo_monto"
    NOTAS = "notas"
    RESUMEN = "resumen"
    EDIT_MENU = "edit_menu"
    PREGUNTA = "pregunta"  # el primer mensaje parecía una pregunta: ¿es pedido o consulta?


class PaymentMethod(StrEnum):
    EFECTIVO = "efectivo"
    NEQUI = "nequi"
    TRANSFERENCIA = "transferencia"


# Un cliente solo puede tener un pedido en alguno de estos estados.
ACTIVE_STATUSES = (
    OrderStatus.BORRADOR,
    OrderStatus.EN_COLA,
    OrderStatus.CONFIRMADO,
    OrderStatus.PREPARANDO,
    OrderStatus.EN_CAMINO,
)

# Estados que cuentan para la posición en la fila.
QUEUE_STATUSES = (OrderStatus.EN_COLA, OrderStatus.CONFIRMADO, OrderStatus.PREPARANDO)

# Desde aquí en adelante el bot ya no responde: atiende el tendero.
HUMAN_STATUSES = (
    OrderStatus.EN_COLA,
    OrderStatus.CONFIRMADO,
    OrderStatus.PREPARANDO,
    OrderStatus.EN_CAMINO,
)

ALLOWED: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.BORRADOR: {OrderStatus.EN_COLA, OrderStatus.CANCELADO},
    OrderStatus.EN_COLA: {OrderStatus.CONFIRMADO, OrderStatus.CANCELADO},
    OrderStatus.CONFIRMADO: {OrderStatus.PREPARANDO, OrderStatus.CANCELADO},
    OrderStatus.PREPARANDO: {OrderStatus.EN_CAMINO, OrderStatus.CANCELADO},
    OrderStatus.EN_CAMINO: {OrderStatus.ENTREGADO, OrderStatus.CANCELADO},
    OrderStatus.ENTREGADO: set(),
    OrderStatus.CANCELADO: set(),
}
