import os

from dotenv import load_dotenv


# Cargar variables de entorno desde el archivo .env en la raíz del proyecto
load_dotenv()


# TOKEN del bot de Telegram (definido en .env como TELEGRAM_TOKEN = "...")
TELEGRAM_TOKEN: str | None = os.getenv("TELEGRAM_TOKEN")
ADMIN_TELEGRAM_ID_RAW = os.getenv("ADMIN_TELEGRAM_ID")

PAGO_MOVIL_TELEFONO = os.getenv("PAGO_MOVIL_TELEFONO", "04127729859")
PAGO_MOVIL_CEDULA = os.getenv("PAGO_MOVIL_CEDULA", "31075856")
PAGO_MOVIL_BANCO = os.getenv("PAGO_MOVIL_BANCO", "Banesco")
PRECIO_MAYOR_UMBRAL = os.getenv("PRECIO_MAYOR_UMBRAL", "10.00")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

# Ruta absoluta a la imagen del catálogo (PNG/JPEG). Si no existe, el bot usa lista en texto.
CATALOGO_VISUAL_PATH: str | None = os.getenv("CATALOGO_VISUAL_PATH")

ADMIN_TELEGRAM_ID: int | None = None
if ADMIN_TELEGRAM_ID_RAW:
	try:
		ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID_RAW)
	except ValueError:
		raise RuntimeError("ADMIN_TELEGRAM_ID debe ser un número entero válido.")

if not TELEGRAM_TOKEN:
	raise RuntimeError(
		"La variable de entorno TELEGRAM_TOKEN no está definida en el archivo .env."
	)
