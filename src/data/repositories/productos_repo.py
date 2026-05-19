import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..database import get_connection

_product_tags_mod: Any = None


def _product_tags():
	"""Carga `core.product_tags` (carpeta `src` en sys.path) sin depender del nombre del paquete raíz."""

	global _product_tags_mod
	if _product_tags_mod is not None:
		return _product_tags_mod
	src_root = Path(__file__).resolve().parents[2]
	r = str(src_root)
	if r not in sys.path:
		sys.path.insert(0, r)
	import core.product_tags as pt  # noqa: PLC0415

	_product_tags_mod = pt
	return _product_tags_mod


def _parse_etiquetas_column(raw: Any) -> list[str]:
	return _product_tags().parse_etiquetas_list(raw)


def _row_producto(row) -> Dict:
	d = dict(row)
	d["etiquetas"] = _parse_etiquetas_column(d.get("etiquetas"))
	d["descripcion"] = (d.get("descripcion") or "").strip()
	return d


def insertar_producto(
	nombre_producto: str,
	precio_detal: float,
	precio_mayor: float,
	cantidad: int,
	descripcion: str = "",
	etiquetas: list[str] | None = None,
) -> int:
	"""Inserta un producto en catálogo y devuelve su ID."""

	pt = _product_tags()
	if etiquetas is None:
		etiquetas = pt.infer_etiquetas_desde_nombre(nombre_producto)
	ej = pt.serialize_etiquetas(etiquetas)
	desc = (descripcion or "").strip()

	with get_connection() as conn:
		cursor = conn.execute(
			"INSERT INTO productos (nombre_producto, precio_detal, precio_mayor, cantidad, descripcion, etiquetas) "
			"VALUES (?, ?, ?, ?, ?, ?)",
			(nombre_producto, precio_detal, precio_mayor, cantidad, desc, ej),
		)
		conn.commit()
		return cursor.lastrowid


def listar_productos_disponibles() -> List[Dict]:
	"""Devuelve productos con stock mayor a cero."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre_producto, precio_detal, precio_mayor, cantidad, "
			"COALESCE(descripcion, '') AS descripcion, COALESCE(etiquetas, '[]') AS etiquetas "
			"FROM productos WHERE cantidad > 0 ORDER BY nombre_producto",
		)
		return [_row_producto(row) for row in cursor.fetchall()]


def listar_productos() -> List[Dict]:
	"""Devuelve todos los productos del catálogo, incluyendo stock 0."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre_producto, precio_detal, precio_mayor, cantidad, "
			"COALESCE(descripcion, '') AS descripcion, COALESCE(etiquetas, '[]') AS etiquetas "
			"FROM productos ORDER BY nombre_producto",
		)
		return [_row_producto(row) for row in cursor.fetchall()]


def obtener_producto_por_nombre(nombre_producto: str) -> Optional[Dict]:
	"""Busca un producto por nombre (sin importar mayúsculas/minúsculas)."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre_producto, precio_detal, precio_mayor, cantidad, "
			"COALESCE(descripcion, '') AS descripcion, COALESCE(etiquetas, '[]') AS etiquetas "
			"FROM productos WHERE nombre_producto = ? COLLATE NOCASE",
			(nombre_producto,),
		)
		row = cursor.fetchone()
		return _row_producto(row) if row else None


def obtener_producto_por_id(producto_id: int) -> Optional[Dict]:
	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre_producto, precio_detal, precio_mayor, cantidad, "
			"COALESCE(descripcion, '') AS descripcion, COALESCE(etiquetas, '[]') AS etiquetas "
			"FROM productos WHERE id = ?",
			(producto_id,),
		)
		row = cursor.fetchone()
		return _row_producto(row) if row else None


def descontar_stock(producto_id: int, unidades: int) -> bool:
	"""Descuenta stock si hay cantidad suficiente; devuelve True si lo logró."""

	with get_connection() as conn:
		cursor = conn.execute(
			"UPDATE productos SET cantidad = cantidad - ? "
			"WHERE id = ? AND cantidad >= ?",
			(unidades, producto_id, unidades),
		)
		conn.commit()
		return cursor.rowcount > 0


def actualizar_precios_producto(nombre_producto: str, nuevo_precio_detal: float | None, nuevo_precio_mayor: float | None) -> bool:
	"""Actualiza precios detal y/o mayor de un producto por nombre (case-insensitive)."""

	if nuevo_precio_detal is None and nuevo_precio_mayor is None:
		return False

	with get_connection() as conn:
		if nuevo_precio_detal is not None and nuevo_precio_mayor is not None:
			cursor = conn.execute(
				"UPDATE productos SET precio_detal = ?, precio_mayor = ? WHERE nombre_producto = ? COLLATE NOCASE",
				(nuevo_precio_detal, nuevo_precio_mayor, nombre_producto),
			)
		elif nuevo_precio_detal is not None:
			cursor = conn.execute(
				"UPDATE productos SET precio_detal = ? WHERE nombre_producto = ? COLLATE NOCASE",
				(nuevo_precio_detal, nombre_producto),
			)
		else:
			cursor = conn.execute(
				"UPDATE productos SET precio_mayor = ? WHERE nombre_producto = ? COLLATE NOCASE",
				(nuevo_precio_mayor, nombre_producto),
			)
		conn.commit()
		return cursor.rowcount > 0


def actualizar_stock_producto(nombre_producto: str, nueva_cantidad: int) -> bool:
	"""Actualiza stock de un producto por nombre (case-insensitive)."""

	with get_connection() as conn:
		cursor = conn.execute(
			"UPDATE productos SET cantidad = ? "
			"WHERE nombre_producto = ? COLLATE NOCASE",
			(nueva_cantidad, nombre_producto),
		)
		conn.commit()
		return cursor.rowcount > 0


def eliminar_producto_por_nombre(nombre_producto: str) -> bool:
	"""Elimina un producto por nombre (case-insensitive)."""

	with get_connection() as conn:
		cursor = conn.execute(
			"DELETE FROM productos WHERE nombre_producto = ? COLLATE NOCASE",
			(nombre_producto,),
		)
		conn.commit()
		return cursor.rowcount > 0


def actualizar_descripcion(nombre_producto: str, descripcion: str) -> bool:
	with get_connection() as conn:
		cur = conn.execute(
			"UPDATE productos SET descripcion = ? WHERE nombre_producto = ? COLLATE NOCASE",
			((descripcion or "").strip(), nombre_producto),
		)
		conn.commit()
		return cur.rowcount > 0


def establecer_etiquetas(nombre_producto: str, etiquetas: list[str]) -> bool:
	ej = _product_tags().serialize_etiquetas(etiquetas)
	with get_connection() as conn:
		cur = conn.execute(
			"UPDATE productos SET etiquetas = ? WHERE nombre_producto = ? COLLATE NOCASE",
			(ej, nombre_producto),
		)
		conn.commit()
		return cur.rowcount > 0


def anadir_etiqueta(nombre_producto: str, etiqueta: str) -> bool:
	p = obtener_producto_por_nombre(nombre_producto)
	if not p:
		return False
	t = (etiqueta or "").strip()
	if not t:
		return False
	tags = list(p.get("etiquetas") or [])
	if t.lower() in {x.lower() for x in tags}:
		return True
	tags.append(t)
	return establecer_etiquetas(nombre_producto, tags)


def rellenar_etiquetas_vacias_desde_nombres() -> int:
	"""Asigna etiquetas inferidas a filas con lista vacía. Idempotente."""

	updated = 0
	with get_connection() as conn:
		cur = conn.execute("SELECT id, nombre_producto, COALESCE(etiquetas, '[]') AS e FROM productos")
		for row in cur.fetchall():
			raw = row["e"]
			try:
				tags = json.loads(raw or "[]")
			except Exception:
				tags = []
			if isinstance(tags, list) and len(tags) > 0:
				continue
			nombre = row["nombre_producto"]
			pt = _product_tags()
			new_tags = pt.infer_etiquetas_desde_nombre(nombre)
			if not new_tags:
				continue
			conn.execute(
				"UPDATE productos SET etiquetas = ? WHERE id = ?",
				(pt.serialize_etiquetas(new_tags), row["id"]),
			)
			updated += 1
		conn.commit()
	return updated


def quitar_etiqueta(nombre_producto: str, etiqueta: str) -> bool:
	p = obtener_producto_por_nombre(nombre_producto)
	if not p:
		return False
	t_norm = _norm_tag(etiqueta)
	tags = [x for x in (p.get("etiquetas") or []) if _norm_tag(x) != t_norm]
	return establecer_etiquetas(nombre_producto, tags)


def _norm_tag(s: str) -> str:
	return (s or "").strip().lower()

