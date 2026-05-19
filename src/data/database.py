import logging
import os
import sqlite3
from pathlib import Path


DB_FILENAME = "chatbot.db"


def get_db_path() -> str:
	"""Devuelve la ruta absoluta del archivo SQLite dentro del proyecto."""

	root_dir = Path(__file__).resolve().parents[2]
	return str(root_dir / DB_FILENAME)


def get_connection() -> sqlite3.Connection:
	"""Crea y devuelve una conexión a la base de datos SQLite."""

	conn = sqlite3.connect(get_db_path())
	conn.execute("PRAGMA foreign_keys = ON")
	conn.row_factory = sqlite3.Row
	return conn


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
	"""Verifica si una columna existe dentro de una tabla."""

	cursor = conn.execute(f"PRAGMA table_info({table_name})")
	columns = cursor.fetchall()
	return any(col[1] == column_name for col in columns)


def _db_object_exists(conn: sqlite3.Connection, name: str) -> bool:
	"""Indica si existe una tabla o vista con el nombre dado."""

	cursor = conn.execute(
		"SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
		(name,),
	)
	return cursor.fetchone() is not None


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
	"""Indica si existe una tabla base (no vista)."""

	cursor = conn.execute(
		"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
		(table_name,),
	)
	return cursor.fetchone() is not None


def _run_compatibility_migrations(conn: sqlite3.Connection) -> None:
	"""Aplica migraciones mínimas para compatibilidad con esquemas anteriores."""

	if not _column_exists(conn, "pedidos", "producto_id"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN producto_id INTEGER")

	if not _column_exists(conn, "pedidos", "cantidad"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN cantidad INTEGER NOT NULL DEFAULT 1")

	if not _column_exists(conn, "pedidos", "tipo_entrega"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN tipo_entrega TEXT NOT NULL DEFAULT 'pickup'")

	if not _column_exists(conn, "pedidos", "ubicacion_entrega"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN ubicacion_entrega TEXT")

	if not _column_exists(conn, "pedidos", "delivery_costo_usd"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN delivery_costo_usd REAL NOT NULL DEFAULT 0")

	if not _column_exists(conn, "pedidos", "delivery_revisado"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN delivery_revisado INTEGER NOT NULL DEFAULT 0")

	if not _column_exists(conn, "pedidos", "metodo_pago"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN metodo_pago TEXT NOT NULL DEFAULT 'presencial'")

	if not _column_exists(conn, "pedidos", "comprobante_file_id"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN comprobante_file_id TEXT")

	if not _column_exists(conn, "pedidos", "stock_descontado"):
		conn.execute("ALTER TABLE pedidos ADD COLUMN stock_descontado INTEGER NOT NULL DEFAULT 0")

	conn.execute(
		"CREATE TABLE IF NOT EXISTS pedido_chat ("
		"id INTEGER PRIMARY KEY AUTOINCREMENT, "
		"pedido_id INTEGER NOT NULL, "
		"emisor TEXT NOT NULL, "
		"mensaje TEXT NOT NULL, "
		"fecha_creacion TEXT NOT NULL DEFAULT (datetime('now')), "
		"FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE"
		")"
	)

	conn.execute(
		"CREATE TABLE IF NOT EXISTS pedido_items ("
		"id INTEGER PRIMARY KEY AUTOINCREMENT, "
		"pedido_id INTEGER NOT NULL, "
		"producto_id INTEGER NOT NULL, "
		"cantidad INTEGER NOT NULL, "
		"precio_unitario REAL NOT NULL, "
		"FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE, "
		"FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE RESTRICT"
		")"
	)

	conn.execute(
		"CREATE TABLE IF NOT EXISTS app_config ("
		"config_key TEXT PRIMARY KEY, "
		"config_value TEXT NOT NULL"
		")"
	)

	# Ensure productos table has mayor/detal price columns.
	if _column_exists(conn, "productos", "id"):
		if not _column_exists(conn, "productos", "precio_detal"):
			conn.execute("ALTER TABLE productos ADD COLUMN precio_detal REAL NOT NULL DEFAULT 0")
		if not _column_exists(conn, "productos", "precio_mayor"):
			conn.execute("ALTER TABLE productos ADD COLUMN precio_mayor REAL NOT NULL DEFAULT 0")
		if not _column_exists(conn, "productos", "descripcion"):
			conn.execute("ALTER TABLE productos ADD COLUMN descripcion TEXT NOT NULL DEFAULT ''")
		if not _column_exists(conn, "productos", "etiquetas"):
			conn.execute("ALTER TABLE productos ADD COLUMN etiquetas TEXT NOT NULL DEFAULT '[]'")
		if _column_exists(conn, "productos", "precio"):
			conn.execute(
				"UPDATE productos "
				"SET precio_detal = COALESCE(NULLIF(precio_detal, 0), precio), "
				"precio_mayor = COALESCE(NULLIF(precio_mayor, 0), precio_detal)"
			)
			conn.execute(
				"UPDATE productos SET precio_mayor = precio_detal WHERE precio_mayor = 0 AND precio_detal > 0"
			)

	if _column_exists(conn, "usuarios", "id"):
		if not _column_exists(conn, "usuarios", "apellido"):
			conn.execute("ALTER TABLE usuarios ADD COLUMN apellido TEXT NOT NULL DEFAULT ''")
		if not _column_exists(conn, "usuarios", "cedula"):
			conn.execute("ALTER TABLE usuarios ADD COLUMN cedula TEXT NOT NULL DEFAULT ''")
		if not _column_exists(conn, "usuarios", "telefono"):
			conn.execute("ALTER TABLE usuarios ADD COLUMN telefono TEXT")
		if not _column_exists(conn, "usuarios", "username_telegram"):
			conn.execute("ALTER TABLE usuarios ADD COLUMN username_telegram TEXT")
		if not _column_exists(conn, "usuarios", "fecha_registro"):
			conn.execute("ALTER TABLE usuarios ADD COLUMN fecha_registro TEXT")
			conn.execute(
				"""
				UPDATE usuarios SET fecha_registro = (
					SELECT MIN(p.fecha_creacion) FROM pedidos p WHERE p.usuario_id = usuarios.id
				)
				WHERE EXISTS (SELECT 1 FROM pedidos p WHERE p.usuario_id = usuarios.id)
				AND (fecha_registro IS NULL OR fecha_registro = '')
				"""
			)
			conn.execute(
				"UPDATE usuarios SET fecha_registro = datetime('now') "
				"WHERE fecha_registro IS NULL OR fecha_registro = ''"
			)

	# --- Requerimientos tesis / panel admin (sin alterar tablas ya definidas en schema.sql) ---
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS admin (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			usuario TEXT NOT NULL UNIQUE,
			"contraseña" TEXT NOT NULL
		)
		"""
	)

	if _table_exists(conn, "pedido_chat") and not _column_exists(conn, "pedido_chat", "polaridad_sentimiento"):
		conn.execute("ALTER TABLE pedido_chat ADD COLUMN polaridad_sentimiento REAL")

	# En el modelo del bot el detalle por línea vive en `pedido_items`; la tesis nombra `pedido_detalle`.
	if _table_exists(conn, "pedido_items") and not _db_object_exists(conn, "pedido_detalle"):
		conn.execute(
			"CREATE VIEW pedido_detalle AS "
			"SELECT id, pedido_id, producto_id, cantidad FROM pedido_items"
		)


def reset_productos_catalogo() -> None:
	"""Elimina todos los productos del catálogo de forma explícita."""

	with get_connection() as conn:
		conn.execute("DELETE FROM productos")
		conn.commit()


def initialize_database() -> None:
	"""Ejecuta el script schema.sql para crear tablas si no existen."""

	root_dir = Path(__file__).resolve().parents[2]
	schema_path = root_dir / "src" / "data" / "migrations" / "schema.sql"

	with open(schema_path, "r", encoding="utf-8") as f:
		schema_sql = f.read()

	with get_connection() as conn:
		conn.executescript(schema_sql)
		_run_compatibility_migrations(conn)
		conn.commit()

	try:
		from .repositories import productos_repo

		n = productos_repo.rellenar_etiquetas_vacias_desde_nombres()
		if n:
			logging.getLogger(__name__).info("Etiquetas inferidas para %s productos", n)
	except Exception as exc:
		logging.getLogger(__name__).warning("Inferencia de etiquetas tras migrar: %s", exc)
