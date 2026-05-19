"""Crea o actualiza un usuario del panel (tabla admin). Contraseña con hash (werkzeug).

Uso (desde la carpeta IA_Chatbot):
  python scripts/upsert_admin.py angelox tu_clave
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(ROOT / ".env")

from src.data.database import get_connection, initialize_database


def main() -> None:
	if len(sys.argv) >= 3:
		usuario = sys.argv[1].strip()
		clave = sys.argv[2]
	else:
		print("Uso: python scripts/upsert_admin.py <usuario> <contraseña>")
		sys.exit(1)
	if not usuario:
		print("Usuario vacío.")
		sys.exit(1)

	initialize_database()
	h = generate_password_hash(clave)
	with get_connection() as conn:
		row = conn.execute("SELECT id FROM admin WHERE usuario = ?", (usuario,)).fetchone()
		if row:
			conn.execute(
				'UPDATE admin SET "contraseña" = ? WHERE usuario = ?',
				(h, usuario),
			)
			print(f"Actualizado: {usuario}")
		else:
			conn.execute(
				'INSERT INTO admin (usuario, "contraseña") VALUES (?, ?)',
				(usuario, h),
			)
			print(f"Insertado: {usuario}")
		conn.commit()


if __name__ == "__main__":
	main()
