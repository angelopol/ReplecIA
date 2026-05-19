"""Validaciones simples para la Etapa 3."""


def es_entero_positivo(texto: str) -> bool:
	"""Devuelve True si el texto representa un entero mayor que cero."""

	try:
		return int(texto) > 0
	except (TypeError, ValueError):
		return False
