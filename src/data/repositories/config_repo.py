from ..database import get_connection


def set_config(config_key: str, config_value: str) -> None:
	"""Crea o actualiza un valor de configuración."""

	with get_connection() as conn:
		conn.execute(
			"INSERT INTO app_config (config_key, config_value) VALUES (?, ?) "
			"ON CONFLICT(config_key) DO UPDATE SET config_value = excluded.config_value",
			(config_key, config_value),
		)
		conn.commit()


def get_config(config_key: str) -> str | None:
	"""Obtiene un valor de configuración por clave."""

	with get_connection() as conn:
		cursor = conn.execute(
			"SELECT config_value FROM app_config WHERE config_key = ?",
			(config_key,),
		)
		row = cursor.fetchone()
		return row[0] if row else None


def delete_config(config_key: str) -> None:
	"""Elimina una clave de configuración si existe."""

	with get_connection() as conn:
		conn.execute(
			"DELETE FROM app_config WHERE config_key = ?",
			(config_key,),
		)
		conn.commit()
