from typing import Dict, List, Sequence

from ..database import get_connection


def _table_has_column(conn, table_name: str, column_name: str) -> bool:
	"""Indica si una tabla tiene una columna específica."""

	cursor = conn.execute(f"PRAGMA table_info({table_name})")
	columns = cursor.fetchall()
	return any(col[1] == column_name for col in columns)


def _table_exists(conn, table_name: str) -> bool:
	"""Indica si una tabla existe en la base de datos."""

	cursor = conn.execute(
		"SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
		(table_name,),
	)
	return cursor.fetchone() is not None


def _producto_precio_unitario(conn, producto_id: int) -> float:
	"""Obtiene un precio unitario de respaldo para compatibilidad con pedidos antiguos."""

	cursor = conn.execute(
		"SELECT precio_detal FROM productos WHERE id = ?",
		(producto_id,),
	)
	row = cursor.fetchone()
	return float(row[0]) if row and row[0] is not None else 0.0


def insertar_pedido(
	usuario_id: int,
	producto_id: int,
	cantidad: int,
	tipo_entrega: str,
	ubicacion_entrega: str | None,
	delivery_costo_usd: float,
	metodo_pago: str,
	comprobante_file_id: str | None,
	estado: str = "pendiente",
	items: Sequence[dict] | None = None,
) -> int:
	"""Inserta un pedido vinculado a productos y devuelve su ID.

	Compatibilidad:
	- Si la base antigua aún tiene columna `producto` con NOT NULL,
	  también la completa con el nombre del producto.
	"""

	with get_connection() as conn:
		if _table_has_column(conn, "pedidos", "producto"):
			cursor = conn.execute(
				"INSERT INTO pedidos ("
				"usuario_id, producto_id, cantidad, tipo_entrega, ubicacion_entrega, "
				"delivery_costo_usd, delivery_revisado, metodo_pago, "
				"comprobante_file_id, estado, producto"
				") VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, (SELECT nombre_producto FROM productos WHERE id = ?))",
				(
					usuario_id,
					producto_id,
					cantidad,
					tipo_entrega,
					ubicacion_entrega,
					delivery_costo_usd,
					metodo_pago,
					comprobante_file_id,
					estado,
					producto_id,
				),
			)
		else:
			cursor = conn.execute(
				"INSERT INTO pedidos ("
				"usuario_id, producto_id, cantidad, tipo_entrega, ubicacion_entrega, delivery_costo_usd, "
				"delivery_revisado, metodo_pago, comprobante_file_id, estado"
				") VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
				(
					usuario_id,
					producto_id,
					cantidad,
					tipo_entrega,
					ubicacion_entrega,
					delivery_costo_usd,
					metodo_pago,
					comprobante_file_id,
					estado,
				),
			)

		pedido_id = cursor.lastrowid

		if _table_exists(conn, "pedido_items"):
			items_a_insertar = list(items) if items else [
				{
					"producto_id": producto_id,
					"cantidad": cantidad,
					"precio_unitario": _producto_precio_unitario(conn, producto_id),
				}
			]

			for item in items_a_insertar:
				conn.execute(
					"INSERT INTO pedido_items (pedido_id, producto_id, cantidad, precio_unitario) VALUES (?, ?, ?, ?)",
					(
						pedido_id,
						int(item["producto_id"]),
						int(item["cantidad"]),
						float(item["precio_unitario"]),
					),
				)

		conn.commit()
		return pedido_id


def obtener_items_pedido(pedido_id: int) -> List[Dict]:
	"""Obtiene las líneas de items asociadas a un pedido."""

	with get_connection() as conn:
		if not _table_exists(conn, "pedido_items"):
			return []

		cursor = conn.execute(
			"SELECT pi.id, pi.pedido_id, pi.producto_id, pr.nombre_producto, pi.cantidad, pi.precio_unitario "
			"FROM pedido_items pi "
			"LEFT JOIN productos pr ON pr.id = pi.producto_id "
			"WHERE pi.pedido_id = ? ORDER BY pi.id ASC",
			(pedido_id,),
		)
		return [dict(row) for row in cursor.fetchall()]


def listar_pedidos_por_usuario(usuario_id: int) -> List[Dict]:
	"""Devuelve la lista de pedidos de un usuario con nombre de producto."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT p.id, p.usuario_id, p.producto_id, pr.nombre_producto, "
			"p.cantidad, p.tipo_entrega, p.ubicacion_entrega, p.delivery_costo_usd, "
			"p.delivery_revisado, "
			"p.metodo_pago, p.comprobante_file_id, "
			"p.estado, p.stock_descontado, p.fecha_creacion "
			"FROM pedidos p "
			"LEFT JOIN productos pr ON p.producto_id = pr.id "
			"WHERE p.usuario_id = ? ORDER BY p.fecha_creacion DESC",
			(usuario_id,),
		)
		rows = cursor.fetchall()
		return [dict(row) for row in rows]


def obtener_pedido_por_id(pedido_id: int) -> Dict | None:
	"""Obtiene un pedido por ID incluyendo datos del usuario y producto."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT p.id, p.usuario_id, u.telegram_id, "
			"TRIM(COALESCE(u.nombre, '') || ' ' || COALESCE(u.apellido, '')) AS usuario_nombre, "
			"u.cedula AS usuario_cedula, u.telefono AS usuario_telefono, "
			"p.producto_id, pr.nombre_producto, p.cantidad, p.tipo_entrega, "
			"p.ubicacion_entrega, p.delivery_costo_usd, p.metodo_pago, p.comprobante_file_id, "
			"p.delivery_revisado, p.estado, p.stock_descontado, p.fecha_creacion "
			"FROM pedidos p "
			"JOIN usuarios u ON u.id = p.usuario_id "
			"LEFT JOIN productos pr ON pr.id = p.producto_id "
			"WHERE p.id = ?",
			(pedido_id,),
		)
		row = cursor.fetchone()
		if not row:
			return None
		pedido = dict(row)
		pedido["items"] = obtener_items_pedido(pedido_id)
		return pedido


def actualizar_estado_pedido(pedido_id: int, estado: str) -> bool:
	"""Actualiza el estado de un pedido."""

	with get_connection() as conn:
		cursor = conn.execute(
			"UPDATE pedidos SET estado = ? WHERE id = ?",
			(estado, pedido_id),
		)
		conn.commit()
		return cursor.rowcount > 0


def actualizar_costo_delivery(pedido_id: int, delivery_costo_usd: float) -> bool:
	"""Actualiza el costo de delivery (USD) en un pedido."""

	with get_connection() as conn:
		cursor = conn.execute(
			"UPDATE pedidos SET delivery_costo_usd = ?, delivery_revisado = 1 WHERE id = ?",
			(delivery_costo_usd, pedido_id),
		)
		conn.commit()
		return cursor.rowcount > 0


def marcar_stock_descontado(pedido_id: int) -> bool:
	"""Marca stock_descontado=1 si aún no estaba marcado."""

	with get_connection() as conn:
		cursor = conn.execute(
			"UPDATE pedidos SET stock_descontado = 1 WHERE id = ? AND stock_descontado = 0",
			(pedido_id,),
		)
		conn.commit()
		return cursor.rowcount > 0


def listar_pedidos_admin() -> List[Dict]:
	"""Lista pedidos para panel/comandos administrativos."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT p.id, TRIM(COALESCE(u.nombre, '') || ' ' || COALESCE(u.apellido, '')) AS usuario_nombre, "
			"u.telegram_id, u.cedula AS usuario_cedula, u.telefono AS usuario_telefono, "
			"pr.nombre_producto, "
			"p.cantidad, p.tipo_entrega, p.ubicacion_entrega, p.delivery_costo_usd, "
			"p.delivery_revisado, "
			"p.metodo_pago, p.estado, p.stock_descontado "
			"FROM pedidos p "
			"JOIN usuarios u ON u.id = p.usuario_id "
			"LEFT JOIN productos pr ON pr.id = p.producto_id "
			"WHERE p.estado IN ('pendiente', 'pendiente_admin', 'pendiente_pickup', 'en_camino') "
			"ORDER BY p.id DESC",
		)
		rows = [dict(row) for row in cursor.fetchall()]
		for pedido in rows:
			pedido["items"] = obtener_items_pedido(pedido["id"])
		return rows


def registrar_mensaje_pedido(
	pedido_id: int,
	emisor: str,
	mensaje: str,
	polaridad: float | None = None,
) -> int:
	"""Registra un mensaje de chat asociado al pedido."""

	with get_connection() as conn:
		if (
			emisor == "cliente"
			and polaridad is not None
			and _table_has_column(conn, "pedido_chat", "polaridad_sentimiento")
		):
			cursor = conn.execute(
				"INSERT INTO pedido_chat (pedido_id, emisor, mensaje, polaridad_sentimiento) "
				"VALUES (?, ?, ?, ?)",
				(pedido_id, emisor, mensaje, float(polaridad)),
			)
		else:
			cursor = conn.execute(
				"INSERT INTO pedido_chat (pedido_id, emisor, mensaje) VALUES (?, ?, ?)",
				(pedido_id, emisor, mensaje),
			)
		conn.commit()
		return cursor.lastrowid


def listar_chat_pedido(pedido_id: int, limite: int = 30) -> List[Dict]:
	"""Devuelve mensajes de chat de un pedido, del más reciente al más antiguo."""

	with get_connection() as conn:
		if _table_has_column(conn, "pedido_chat", "polaridad_sentimiento"):
			cursor = conn.execute(
				"SELECT id, pedido_id, emisor, mensaje, fecha_creacion, polaridad_sentimiento "
				"FROM pedido_chat WHERE pedido_id = ? "
				"ORDER BY id DESC LIMIT ?",
				(pedido_id, limite),
			)
		else:
			cursor = conn.execute(
				"SELECT id, pedido_id, emisor, mensaje, fecha_creacion "
				"FROM pedido_chat WHERE pedido_id = ? "
				"ORDER BY id DESC LIMIT ?",
				(pedido_id, limite),
			)
		rows = cursor.fetchall()
		return [dict(row) for row in rows]
