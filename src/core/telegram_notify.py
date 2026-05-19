"""Notificación al cliente por la API HTTP de Telegram (mismo dominio que el bot)."""

import json
import urllib.error
import urllib.parse
import urllib.request

from ..config.settings import TELEGRAM_TOKEN
from ..data.repositories.pedidos_repo import obtener_pedido_por_id


class TelegramNotifyError(Exception):
	"""Error al llamar a sendMessage (red, token o respuesta no ok)."""


def enviar_mensaje_telegram(telegram_id: int, texto: str) -> None:
	"""POST sendMessage; no modifica estado del pedido si falla."""

	if not TELEGRAM_TOKEN:
		raise TelegramNotifyError("TELEGRAM_TOKEN no está configurado.")
	if not texto or not texto.strip():
		raise TelegramNotifyError("El mensaje está vacío.")

	url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
	payload = urllib.parse.urlencode(
		{
			"chat_id": str(int(telegram_id)),
			"text": texto.strip(),
			"disable_web_page_preview": "true",
		}
	).encode("utf-8")
	req = urllib.request.Request(url, data=payload, method="POST")

	try:
		with urllib.request.urlopen(req, timeout=20) as resp:
			raw = resp.read().decode("utf-8", errors="replace")
	except urllib.error.HTTPError as exc:
		try:
			body = exc.read().decode("utf-8", errors="replace")
			data = json.loads(body)
			desc = data.get("description") or body
		except Exception:
			desc = str(exc)
		raise TelegramNotifyError(str(desc)) from exc
	except OSError as exc:
		raise TelegramNotifyError(str(exc)) from exc

	try:
		data = json.loads(raw)
	except json.JSONDecodeError as exc:
		raise TelegramNotifyError("Respuesta inválida de Telegram.") from exc

	if not data.get("ok"):
		raise TelegramNotifyError(str(data.get("description", data)))


def _linea_producto(pedido: dict) -> str:
	nombre = (pedido.get("nombre_producto") or "Producto").strip()
	try:
		cant = int(pedido.get("cantidad") or 1)
	except (TypeError, ValueError):
		cant = 1
	return f"{nombre} ×{cant}"


def texto_cliente_pedido_en_camino(pedido_id: int) -> str:
	p = obtener_pedido_por_id(pedido_id)
	if not p:
		return (
			f"Tu pedido #{pedido_id} fue confirmado y va en camino. "
			f"Si tienes dudas, escribe /soporte {pedido_id} <mensaje>."
		)
	return (
		f"Tu pedido #{pedido_id} está en camino.\n"
		f"{_linea_producto(p)}\n"
		"Te avisamos cuando lo marquemos como entregado. ¡Gracias por elegirnos!"
	)


def texto_cliente_pedido_entregado(pedido_id: int) -> str:
	p = obtener_pedido_por_id(pedido_id)
	if not p:
		return f"Tu pedido #{pedido_id} fue marcado como entregado. ¡Gracias!"
	return (
		f"Tu pedido #{pedido_id} quedó registrado como entregado.\n"
		f"{_linea_producto(p)}\n"
		"Esperamos verte pronto."
	)


def texto_cliente_pedido_concluido_pickup(pedido_id: int) -> str:
	p = obtener_pedido_por_id(pedido_id)
	if not p:
		return f"Tu pedido #{pedido_id} (retiro en tienda) fue concluido. ¡Gracias!"
	return (
		f"Tu pedido #{pedido_id} (retiro en tienda) quedó concluido.\n"
		f"{_linea_producto(p)}\n"
		"Gracias por tu compra."
	)
