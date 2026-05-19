"""Reglas mínimas de transición para el flujo de pedido."""

from .constants import PIDIENDO_PRODUCTO, PIDIENDO_CANTIDAD, CONFIRMANDO_PEDIDO


TRANSICIONES_VALIDAS = {
	PIDIENDO_PRODUCTO: {PIDIENDO_CANTIDAD},
	PIDIENDO_CANTIDAD: {CONFIRMANDO_PEDIDO},
	CONFIRMANDO_PEDIDO: set(),
}


def puede_transicionar(origen: int, destino: int) -> bool:
	"""Indica si el estado destino es válido desde el estado origen."""

	return destino in TRANSICIONES_VALIDAS.get(origen, set())
