"""Etiquetas y descripción de productos: inferencia desde el nombre y utilidades."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

_STOP_NOMBRE = frozenset(
	{
		"de",
		"del",
		"la",
		"el",
		"los",
		"las",
		"un",
		"una",
		"unos",
		"unas",
		"y",
		"e",
		"o",
		"u",
		"en",
		"con",
		"por",
		"para",
		"al",
		"a",
		"ml",
		"cc",
	}
)

# Si aparece la clave, se añaden también los valores como etiquetas extra.
_SINONIMOS_POR_TOKEN: dict[str, tuple[str, ...]] = {
	"cono": ("barquilla", "barquillas"),
	"conos": ("barquilla", "barquillas"),
	"tina": ("potecito", "potecitos"),
	"tinas": ("potecito", "potecitos"),
	"litro": ("pote", "potes"),
	"litros": ("pote", "potes"),
	"helado": ("helados",),
	"super": ("grande",),
	"maxi": ("grande",),
	"mini": ("pequeno", "pequeño"),
}

_SABORES_COMUNES = frozenset(
	{
		"chocolate",
		"vainilla",
		"fresa",
		"menta",
		"limon",
		"ron",
		"pasas",
		"arequipe",
		"dulce",
		"leche",
		"crema",
		"cookies",
		"oreo",
		"cafe",
		"frutos",
		"rojos",
		"maracuya",
		"guayaba",
		"coco",
		"mantecado",
	}
)

_BUSQUEDA_EXCLUIR = frozenset(
	{
		"quiero",
		"dame",
		"necesito",
		"pedido",
		"comprar",
		"encargar",
		"helado",
		"helados",
		"paleta",
		"paletas",
		"producto",
		"productos",
		"catalogo",
		"catalogo",
		"lista",
		"mostrar",
		"ver",
		"unos",
		"unas",
		"algunos",
		"algunas",
		"tambien",
		"también",
		"mas",
		"más",
		"delivery",
		"pickup",
		"domicilio",
		"retiro",
		"llevar",
		"gracias",
	}
)


def _norm_simple(s: str) -> str:
	t = (s or "").strip().lower()
	if not t:
		return ""
	t = unicodedata.normalize("NFKD", t)
	t = "".join(c for c in t if not unicodedata.combining(c))
	t = re.sub(r"[^\w\s]", " ", t)
	return re.sub(r"\s+", " ", t).strip()


def parse_etiquetas_list(raw: Any) -> list[str]:
	"""Convierte columna DB (JSON o texto) en lista de etiquetas normalizadas para mostrar."""

	if raw is None:
		return []
	if isinstance(raw, list):
		base = raw
	elif isinstance(raw, str):
		s = raw.strip()
		if not s:
			return []
		try:
			base = json.loads(s)
		except json.JSONDecodeError:
			base = [x.strip() for x in s.split(",") if x.strip()]
	else:
		return []
	out: list[str] = []
	for x in base:
		if x is None:
			continue
		sx = str(x).strip()
		if sx:
			out.append(sx)
	return out


def serialize_etiquetas(tags: list[str]) -> str:
	"""Serializa etiquetas únicas ordenadas para guardar en SQLite."""

	seen: set[str] = set()
	uniq: list[str] = []
	for t in tags:
		k = _norm_simple(t)
		if len(k) < 2 or k in seen:
			continue
		seen.add(k)
		uniq.append(t.strip())
	uniq.sort(key=lambda z: z.lower())
	return json.dumps(uniq, ensure_ascii=False)


def infer_etiquetas_desde_nombre(nombre_producto: str) -> list[str]:
	"""Genera etiquetas iniciales a partir del nombre (sabores, formato, sinónimos regionales)."""

	n = _norm_simple(nombre_producto)
	if not n:
		return []
	tags: set[str] = set()
	for tok in re.findall(r"\w+", n):
		if len(tok) < 2 or tok in _STOP_NOMBRE:
			continue
		tags.add(tok)
		for extra in _SINONIMOS_POR_TOKEN.get(tok, ()):
			tags.add(_norm_simple(extra))
	# Números + cc / lt comunes en nombres
	for m in re.finditer(r"\b\d+\s*cc\b", n):
		tags.add(m.group(0).replace(" ", ""))
	for m in re.finditer(r"\b\d+\s*lt\b", n):
		tags.add(m.group(0).replace(" ", ""))
	# Palabras compuestas tipo "super cono"
	if "super" in n and "cono" in n:
		tags.add("super")
		tags.add("grande")
	# Sabor explícito en nombre
	for s in _SABORES_COMUNES:
		if _norm_simple(s) in n:
			tags.add(_norm_simple(s))
	return sorted(tags, key=lambda z: z.lower())


def significant_tokens_busqueda(normalized_text: str) -> list[str]:
	"""Tokens útiles para recomendar por característica (excluye pedidos genéricos)."""

	tn = _norm_simple(normalized_text)
	if not tn:
		return []
	out: list[str] = []
	for tok in re.findall(r"\w+", tn):
		if len(tok) < 3 or tok in _BUSQUEDA_EXCLUIR:
			continue
		if tok not in out:
			out.append(tok)
	return out


def etiquetas_resumen_linea(tags: list[str], max_len: int = 48) -> str:
	if not tags:
		return ""
	s = ", ".join(tags[:8])
	if len(s) > max_len:
		s = s[: max_len - 1] + "…"
	return f" [{s}]"
