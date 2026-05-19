"""Módulo simple de análisis de sentimiento usando TextBlob.

TextBlob está entrenado principalmente en inglés: insultos o reclamos en español
suelen quedar en ~0. Por eso combinamos polaridad TextBlob con un léxico liviano
de frustración / reclamo en español (sin sustituir juicio humano del admin).
"""

import re
import unicodedata

from textblob import TextBlob

# Polaridad fija si hay coincidencia léxica (se combina con TextBlob vía min()).
_POLARIDAD_LEXICO = -0.42

_RAW_LEXEMAS = (
	"mierda",
	"mierdas",
	"basura",
	"estafa",
	"estafadores",
	"estafador",
	"ladrones",
	"ladron",
	"robo",
	"mentira",
	"mentiroso",
	"mentirosos",
	"asco",
	"horrible",
	"terrible",
	"fatal",
	"pesimo",
	"decepcion",
	"decepcionado",
	"decepcionada",
	"indignado",
	"indignada",
	"reclamo",
	"queja",
	"problema",
	"inaceptable",
	"verguenza",
	"verguenzas",
	"abuso",
	"abusivos",
	"idiota",
	"idiotas",
	"imbecil",
	"estupido",
	"estupida",
	"estupidos",
	"malparido",
	"malparida",
	"hdp",
	"carajo",
	"cono",
	"puta",
	"puto",
	"putas",
	"putos",
	"cabron",
	"culero",
	"culera",
	"verga",
	"pendejo",
	"pendeja",
	"pendejos",
	"animal",
	"animales",
	"odio",
	"odias",
	"nojoda",
	"marico",
	"marica",
	"burro",
	"burra",
)

_FRASES_NEGATIVAS = (
	"no me gusta",
	"no me gusto",
	"no vuelvo",
	"nunca mas",
	"nunca más",
	"muy mal",
	"mal servicio",
	"pesimo servicio",
	"son unos",
	"hijos de",
	"me estafaron",
	"me robaron",
	"no sirve",
	"no sirven",
	"que asco",
	"dan asco",
)


def _normalizar_sin_tildes(texto: str) -> str:
	t = (texto or "").lower()
	t = unicodedata.normalize("NFD", t)
	return "".join(c for c in t if unicodedata.category(c) != "Mn")


_LEXEMAS_NEGATIVOS = frozenset(_normalizar_sin_tildes(w) for w in _RAW_LEXEMAS)


def _polaridad_lexico_es(texto: str) -> float:
	"""Devuelve _POLARIDAD_LEXICO si hay señales claras en español; si no, 0.0."""

	if not (texto or "").strip():
		return 0.0
	norm = _normalizar_sin_tildes(texto)
	for frase in _FRASES_NEGATIVAS:
		if _normalizar_sin_tildes(frase) in norm:
			return _POLARIDAD_LEXICO
	tokens = set(re.findall(r"\w+", norm, flags=re.UNICODE))
	if tokens & _LEXEMAS_NEGATIVOS:
		return _POLARIDAD_LEXICO
	return 0.0


def _polaridad_textblob(texto: str) -> float:
	try:
		return float(TextBlob(texto).sentiment.polarity)
	except Exception:
		return 0.0


def analizar_sentimiento(texto: str) -> float:
	"""Devuelve la polaridad del texto (float entre -1.0 y 1.0).

	Combina TextBlob con léxico en español para reclamos/insultos frecuentes.
	"""
	if not texto:
		return 0.0

	tb = _polaridad_textblob(texto)
	lex = _polaridad_lexico_es(texto)
	if lex < 0.0:
		return min(tb, lex)
	return tb


def es_negativo(texto: str) -> bool:
	"""Conveniencia: True si la polaridad es claramente negativa."""
	return analizar_sentimiento(texto) < -0.05


def tiene_senal_negativa_es(texto: str) -> bool:
	"""True si hay reclamo/insulto probable en español (léxico o polaridad combinada)."""
	return analizar_sentimiento(texto) < -0.05
