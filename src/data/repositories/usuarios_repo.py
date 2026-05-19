import re
from typing import Literal, Optional

from ..database import get_connection


def obtener_usuario_por_telegram_id(telegram_id: int) -> Optional[dict]:
	"""Devuelve un usuario como dict, o None si no existe."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre, apellido, cedula, telefono, telegram_id, username_telegram, fecha_registro "
			"FROM usuarios WHERE telegram_id = ?",
			(telegram_id,),
		)
		row = cursor.fetchone()
		return dict(row) if row else None


def nombre_publico_usuario(row: dict) -> str:
	"""Nombre legible para saludos y pedidos (nombre puede incluir apellidos)."""

	nombre = str(row.get("nombre") or "").strip()
	apellido = str(row.get("apellido") or "").strip()
	if nombre and apellido:
		return f"{nombre} {apellido}".strip()
	return nombre or apellido


def _cedula_solo_digitos(valor: str | None) -> str:
	return re.sub(r"\D", "", valor or "")


def usuario_perfil_completo(telegram_id: int) -> bool:
	"""True si hay nombre completo (≥2 caracteres) y cédula válida (6–12 dígitos). El teléfono es opcional."""

	row = obtener_usuario_por_telegram_id(telegram_id)
	if not row:
		return False
	nombre_completo = nombre_publico_usuario(row)
	ced = _cedula_solo_digitos(str(row.get("cedula") or ""))
	return len(nombre_completo) >= 2 and 6 <= len(ced) <= 12


def siguiente_paso_registro_incompleto(telegram_id: int) -> Literal["nombre", "cedula"] | None:
	"""Qué falta guardar en perfil, o None si ya está completo."""

	row = obtener_usuario_por_telegram_id(telegram_id)
	if not row:
		return "nombre"
	nombre_completo = nombre_publico_usuario(row)
	if len(nombre_completo.strip()) < 2:
		return "nombre"
	ced = _cedula_solo_digitos(str(row.get("cedula") or ""))
	if not (6 <= len(ced) <= 12):
		return "cedula"
	return None


def guardar_nombre_completo_cliente(telegram_id: int, nombre_completo: str) -> None:
	"""Guarda nombre y apellido en una sola línea de uso frecuente: todo en `nombre`, `apellido` vacío."""

	texto = (nombre_completo or "").strip()
	with get_connection() as conn:
		conn.execute(
			"UPDATE usuarios SET nombre = ?, apellido = ? WHERE telegram_id = ?",
			(texto, "", telegram_id),
		)
		conn.commit()


def guardar_cedula_cliente(telegram_id: int, cedula_digitos: str) -> None:
	"""Guarda solo la cédula normalizada a dígitos."""

	with get_connection() as conn:
		conn.execute(
			"UPDATE usuarios SET cedula = ? WHERE telegram_id = ?",
			(cedula_digitos.strip(), telegram_id),
		)
		conn.commit()


def asegurar_usuario_telegram(telegram_id: int, username_telegram: str | None = None) -> dict:
	"""Crea fila mínima si no existe y devuelve el usuario actualizado."""

	row = obtener_usuario_por_telegram_id(telegram_id)
	if row:
		if username_telegram is not None:
			with get_connection() as conn:
				conn.execute(
					"UPDATE usuarios SET username_telegram = ? WHERE telegram_id = ?",
					(username_telegram, telegram_id),
				)
				conn.commit()
			row = obtener_usuario_por_telegram_id(telegram_id)
		return row

	with get_connection() as conn:
		conn.execute(
			"INSERT INTO usuarios (nombre, apellido, cedula, telefono, telegram_id, username_telegram, fecha_registro) "
			"VALUES (?, '', '', NULL, ?, ?, datetime('now'))",
			("", telegram_id, username_telegram),
		)
		conn.commit()
	row = obtener_usuario_por_telegram_id(telegram_id)
	if row is None:
		raise RuntimeError("No se pudo crear el usuario en base de datos.")
	return row


def actualizar_perfil_cliente(
	telegram_id: int,
	nombre: str,
	apellido: str,
	cedula: str,
	telefono: str | None = None,
) -> None:
	"""Guarda nombre, apellido, cédula y teléfono opcional."""

	with get_connection() as conn:
		conn.execute(
			"UPDATE usuarios SET nombre = ?, apellido = ?, cedula = ?, telefono = ? WHERE telegram_id = ?",
			(nombre.strip(), (apellido or "").strip(), (cedula or "").strip(), telefono, telegram_id),
		)
		conn.commit()


def actualizar_telefono_cliente(telegram_id: int, telefono: str | None) -> None:
	"""Actualiza solo el teléfono (puede ser NULL)."""

	with get_connection() as conn:
		conn.execute(
			"UPDATE usuarios SET telefono = ? WHERE telegram_id = ?",
			(telefono, telegram_id),
		)
		conn.commit()


def contar_usuarios_total() -> int:
	"""Total de filas en usuarios."""

	with get_connection() as conn:
		row = conn.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()
		return int(row["c"] if row else 0)


def listar_usuarios_panel_pagina(pagina: int, por_pagina: int) -> tuple[list[dict], int]:
	"""Lista usuarios para el panel web con paginación (orden por id descendente)."""

	pagina = max(1, int(pagina))
	por_pagina = min(100, max(1, int(por_pagina)))
	offset = (pagina - 1) * por_pagina
	total = contar_usuarios_total()
	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre, apellido, cedula, telefono, telegram_id, username_telegram, "
			"COALESCE(fecha_registro, '') AS fecha_registro "
			"FROM usuarios ORDER BY id DESC LIMIT ? OFFSET ?",
			(por_pagina, offset),
		)
		return [dict(row) for row in cursor.fetchall()], total


def listar_usuarios_panel(limite: int = 500) -> list[dict]:
	"""Lista los primeros N usuarios (sin paginar; tope 500 por rendimiento)."""

	lim = min(max(1, int(limite)), 500)
	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT id, nombre, apellido, cedula, telefono, telegram_id, username_telegram, "
			"COALESCE(fecha_registro, '') AS fecha_registro "
			"FROM usuarios ORDER BY id DESC LIMIT ?",
			(lim,),
		)
		return [dict(row) for row in cursor.fetchall()]


def crear_usuario(nombre: str, telegram_id: int) -> int:
	"""Compatibilidad: crea o fusiona usuario mínimo (pedidos antiguos)."""

	row = obtener_usuario_por_telegram_id(telegram_id)
	if row:
		if nombre and not str(row.get("nombre") or "").strip():
			with get_connection() as conn:
				conn.execute("UPDATE usuarios SET nombre = ? WHERE telegram_id = ?", (nombre.strip(), telegram_id))
				conn.commit()
		return int(row["id"])
	row = asegurar_usuario_telegram(telegram_id, None)
	if nombre:
		with get_connection() as conn:
			conn.execute("UPDATE usuarios SET nombre = ? WHERE telegram_id = ?", (nombre.strip(), telegram_id))
			conn.commit()
	return int(row["id"])
