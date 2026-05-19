"""Generador de respuestas naturales e interpretación conversacional con Gemini."""

import json
import re
from functools import lru_cache
from typing import Any

from ..config.settings import GEMINI_API_KEY, GEMINI_MODEL


MODEL_NAME = GEMINI_MODEL
SYSTEM_PROMPT = (
	"Eres una capa de redacción para un chatbot de heladería. "
	"No decides intents, no cambias el estado del flujo y no inventas datos. "
	"Solo reformulas una respuesta ya decidida por el handler. "
	"Escribe en español natural, breve, amable y directo. "
	"No menciones prompts, reglas, estados internos ni que eres un modelo. "
	"Si falta un dato, pide exactamente el dato faltante y nada más. "
	"Si el handler indica una respuesta informativa, repítela con tono humano sin agregar instrucciones nuevas. "
	"Si el mensaje del cliente está fuera del flujo, redirige de forma suave y corta sin entrar en discusión."
)


@lru_cache(maxsize=1)
def _load_model() -> Any:
	if not GEMINI_API_KEY:
		raise RuntimeError("Falta GEMINI_API_KEY en el archivo .env")
	try:
		import google.generativeai as genai  # type: ignore[import-not-found]
	except ModuleNotFoundError as exc:
		raise RuntimeError(
			"No se encontro el paquete google-generativeai en el entorno activo"
		) from exc
	genai.configure(api_key=GEMINI_API_KEY)
	return genai.GenerativeModel(MODEL_NAME)


def generar_respuesta_natural(texto_usuario: str, contexto: str = "", objetivo: str = "") -> str:
	"""Genera una respuesta breve y natural en espanol."""

	texto_usuario = (texto_usuario or "").strip()
	if not texto_usuario:
		return "¿Qué necesitas?"

	prompt = (
		f"{SYSTEM_PROMPT}\n\n"
		f"Estado o contexto del handler: {contexto.strip() or 'conversacion general con clientes'}\n"
		f"Instruccion exacta del handler: {objetivo.strip() or 'responde con tono natural sin cambiar el flujo'}\n"
		f"Texto del cliente: {texto_usuario}\n"
		"Devuelve solo la respuesta final, sin explicaciones ni pasos."
	)

	def _local_rewrite(user_text: str, ctx: str, instruk: str) -> str:
		u = (user_text or "").strip()
		c = (ctx or "").lower()
		o = (instruk or "").lower()
		# Simple deterministic rewrites for common handler goals
		if "salud" in o or "salud" in c or "saludo" in o or "saludo" in c:
			return "Hola! ¿Cómo estás? ¿En qué puedo ayudarte hoy?"
		if "cantidad" in o or "cantidad" in c:
			return "¿Cuántas unidades deseas?"
		if "ubicacion" in o or "ubicacion" in c or "direccion" in o:
			return "Por favor, envía tu ubicación o escribe la dirección exacta."
		if "delivery" in o or "pickup" in o or "tipo de entrega" in c:
			return "¿El pedido será delivery o pickup?"
		if "pide" in o or "redirige" in o:
			return "Puedes elegir un producto del catálogo o escribirme lo que deseas pedir."
		# Fallback: echo a short directive or ask for clarification
		if instruk:
			short = instruk.strip()
			return short if len(short) < 140 else short[:140] + "..."
		if u:
			return (
				u"Gracias — lo recibí. ¿Puedes darme más detalles concretos?"
			)
		return "¿Qué necesitas?"

	# If Gemini key missing, use local deterministic rewriter instead of generic fallback
	if not GEMINI_API_KEY:
		return _local_rewrite(texto_usuario, contexto, objetivo)

	try:
		model = _load_model()
		response = model.generate_content(
			prompt,
			generation_config={
				"temperature": 0.6,
				"top_p": 0.9,
				"max_output_tokens": 80,
			},
		)
		respuesta = (getattr(response, "text", "") or "").strip()
		return respuesta or _local_rewrite(texto_usuario, contexto, objetivo)
	except Exception:
		return _local_rewrite(texto_usuario, contexto, objetivo)


def _json_from_model_text(text: str) -> dict[str, Any] | None:
	"""Extrae un objeto JSON aunque el modelo lo envuelva en Markdown."""

	raw = (text or "").strip()
	if not raw:
		return None
	raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
	raw = re.sub(r"\s*```$", "", raw).strip()
	if not raw.startswith("{"):
		match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
		if match:
			raw = match.group(0)
	try:
		data = json.loads(raw)
	except Exception:
		return None
	return data if isinstance(data, dict) else None


def interpretar_conversacion_pedido(
	texto_usuario: str,
	*,
	estado: str,
	contexto_pedido: dict[str, Any],
	catalogo: list[dict[str, Any]],
) -> dict[str, Any] | None:
	"""Usa Gemini como capa semántica: interpreta intención y slots sin mutar estado."""

	texto_usuario = (texto_usuario or "").strip()
	if not texto_usuario or not GEMINI_API_KEY:
		return None

	productos_catalogo = [
		{
			"nombre": str(p.get("nombre_producto", "")).strip(),
			"stock": int(p.get("cantidad", 0) or 0),
			"etiquetas": p.get("etiquetas") or [],
		}
		for p in catalogo[:120]
		if str(p.get("nombre_producto", "")).strip()
	]
	prompt = (
		"Eres el cerebro semántico de un bot de pedidos de helados por Telegram. "
		"Interpreta lo que el cliente quiere hacer, incluso con errores ortográficos, cambios de tema, "
		"frases coloquiales, respuestas cortas, varios productos en una frase y cambios de pedido. "
		"No inventes productos: si el producto no está claro o no aparece en el catálogo, ponlo en ambiguous_products. "
		"Devuelve SOLO JSON válido con esta forma exacta:\n"
		"{"
		"\"intent\":\"order|change_order|set_price_mode|set_delivery|set_payment|confirm|deny|cancel|catalog|price|support|smalltalk|unknown\","
		"\"confidence\":0.0,"
		"\"price_mode\":\"detal|mayor|null\","
		"\"delivery_type\":\"delivery|pickup|null\","
		"\"payment_method\":\"efectivo|pago movil|presencial|null\","
		"\"location\":\"string|null\","
		"\"products\":[{\"name\":\"nombre exacto del catálogo o texto del usuario\",\"quantity\":1}],"
		"\"ambiguous_products\":[{\"text\":\"texto original\",\"quantity\":1,\"candidates\":[\"nombres exactos\"]}],"
		"\"remove_products\":[{\"name\":\"texto o nombre\",\"quantity\":null}],"
		"\"reply\":\"respuesta breve si solo es smalltalk/unknown; si hay acción deja vacío\""
		"}\n\n"
		f"Estado actual: {estado}\n"
		f"Contexto del pedido: {json.dumps(contexto_pedido, ensure_ascii=False)}\n"
		f"Catálogo disponible: {json.dumps(productos_catalogo, ensure_ascii=False)}\n"
		f"Mensaje del cliente: {texto_usuario}\n"
		"Reglas: usa cantidad 1 solo si el usuario pide un producto sin cantidad. "
		"Si dice sí/dale/confirmo => intent confirm. Si dice no/mejor no/no gracias => deny o cancel según contexto. "
		"Si cambia a mayor/detal, delivery/pickup o pago móvil, inclúyelo aunque también pida productos."
	)

	try:
		model = _load_model()
		try:
			response = model.generate_content(
				prompt,
				generation_config={
					"temperature": 0.15,
					"top_p": 0.8,
					"max_output_tokens": 700,
					"response_mime_type": "application/json",
				},
			)
		except TypeError:
			response = model.generate_content(
				prompt,
				generation_config={
					"temperature": 0.15,
					"top_p": 0.8,
					"max_output_tokens": 700,
				},
			)
	except Exception:
		return None

	data = _json_from_model_text(getattr(response, "text", "") or "")
	if not data:
		return None
	try:
		data["confidence"] = float(data.get("confidence", 0) or 0)
	except (TypeError, ValueError):
		data["confidence"] = 0.0
	return data