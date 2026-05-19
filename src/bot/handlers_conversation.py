"""Flujo de conversación de ejemplo para hacer un pedido."""

import asyncio
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher

from telegram import (
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	InputFile,
	KeyboardButton,
	ReplyKeyboardMarkup,
	ReplyKeyboardRemove,
	Update,
)
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ConversationHandler, ContextTypes

from ..config.settings import (
	ADMIN_TELEGRAM_ID,
	CATALOGO_VISUAL_PATH,
	PAGO_MOVIL_TELEFONO,
)
from ..core.constants import (
	PIDIENDO_PRODUCTO,
	PIDIENDO_CANTIDAD,
	PIDIENDO_TIPO_ENTREGA,
	PIDIENDO_UBICACION,
	ESPERANDO_COSTO_DELIVERY,
	PIDIENDO_METODO_PAGO,
	PIDIENDO_COMPROBANTE,
	CONFIRMANDO_PEDIDO,
	PIDIENDO_MODO_PRECIO,
	PIDIENDO_NOMBRE_CLIENTE,
	PIDIENDO_CEDULA_CLIENTE,
	PIDIENDO_TELEFONO_CLIENTE,
)
from ..core.product_tags import etiquetas_resumen_linea
from ..core.telegram_notify import (
	TelegramNotifyError,
	enviar_mensaje_telegram,
	texto_cliente_pedido_concluido_pickup,
	texto_cliente_pedido_en_camino,
	texto_cliente_pedido_entregado,
)
from ..core.services.pedidos_service import (
	admin_asignar_costo_delivery,
	admin_actualizar_precios,
	admin_actualizar_stock,
	admin_crear_producto,
	admin_eliminar_producto,
	admin_etiqueta_anadir,
	admin_etiqueta_quitar,
	admin_inferir_etiquetas_todos,
	admin_set_descripcion_producto,
	admin_set_etiquetas_producto,
	admin_concluir_pickup,
	admin_confirmar_delivery,
	admin_marcar_entregado_delivery,
	crear_pedido,
	actualizar_datos_pago_movil,
	guardar_costo_delivery_pendiente,
	obtener_costo_delivery_pendiente,
	limpiar_costo_delivery_pendiente,
	obtener_delivery_pendiente,
	preparar_delivery_pendiente,
	obtener_chat_admin,
	obtener_catalogo_admin,
	obtener_datos_pago_movil,
	obtener_resumen_montos,
	obtener_tasa_usd_bcv,
	validar_minimo_compra_mayor,
	obtener_umbral_precio_mayor_usd,
	recomendar_productos_por_consulta,
	obtener_pedidos_admin,
	obtener_pedido_por_id,
	obtener_catalogo_disponible,
	obtener_producto_disponible_por_nombre,
	registrar_mensaje_admin,
	registrar_mensaje_cliente,
)
from ..data.repositories.usuarios_repo import (
	asegurar_usuario_telegram,
	actualizar_telefono_cliente,
	guardar_cedula_cliente,
	guardar_nombre_completo_cliente,
	nombre_publico_usuario,
	obtener_usuario_por_telegram_id,
	siguiente_paso_registro_incompleto,
	usuario_perfil_completo,
)
from ..ia.response_generator import generar_respuesta_natural, interpretar_conversacion_pedido
from ..ia.sentiment_analyzer import analizar_sentimiento, tiene_senal_negativa_es
from ..ia.nlu import (
	normalize_text,
	split_instructions,
	detect_intent,
	_build_product_aliases,
	_spanish_token_variants,
	_NUM_WORDS,
	_ORDER_CUES,
	_etiqueta_aliases_from_product,
	apply_colloquial_helado_terms,
	list_unknown_product_terms,
	strip_control_commands_for_product_search,
)
from ..core.validators import es_entero_positivo

_log = logging.getLogger(__name__)

# Texto visible al cliente (capturas / tesis): tono formal, sin exponer rol interno.
_MSG_SOLICITAR_UBICACION_DELIVERY = (
	"Para envío a domicilio envíe su dirección completa o comparta la ubicación en el mapa."
)
_TEXTO_RESUMEN_COSTO_ENVIO_PENDIENTE = "- Costo de envío: por confirmar (le avisaremos el monto en breve)."
_TEXTO_CLIENTE_TRAS_ASIGNAR_ENVIO = "Elija método de pago (también puede escribir efectivo o pago móvil)."

_GENERIC_PRODUCT_TOKENS = {
	"helado",
	"helados",
	"paleta",
	"paletas",
	"tina",
	"tinas",
	"cono",
	"conos",
	"producto",
	"productos",
	"pedido",
	"pedidos",
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
}
_NUMBER_TOKEN_RE = r"(?:\d+|" + "|".join(re.escape(word) for word in _NUM_WORDS.keys()) + r")"
_NON_PRODUCT_REPLY_PHRASES = {
	"eso es todo",
	"mas nada",
	"nada mas",
	"nada mas gracias",
	"gracias",
	"muchas gracias",
	"ok",
	"oki",
	"okay",
	"dale",
	"listo",
	"perfecto",
	"esta bien",
	"está bien",
	"entendido",
}
_CART_FINISH_PHRASES = {
	"eso es todo",
	"eso seria todo",
	"eso sería todo",
	"ya no quiero mas",
	"ya no quiero más",
	"no quiero mas",
	"no quiero más",
	"nada mas",
	"nada más",
	"ya esta",
	"ya está",
	"quiero ordenar",
	"quiero pedir",
	"continuar pedido",
	"seguir con el pedido",
	"terminar pedido",
	"cerrar",
	"finalizar",
	"terminar",
}


async def _answer_callback_query_safe(query, **kwargs) -> None:
	"""Evita que un timeout de red al contestar el inline deje el handler a medias."""

	if query is None:
		return
	try:
		await query.answer(**kwargs)
	except (TimedOut, NetworkError) as exc:
		_log.warning("No se pudo answer_callback_query (red/timeout): %s", exc)
	except BadRequest:
		pass


def _limpiar_datos_pedido(context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Limpia datos del pedido en curso (no toca registro de cliente)."""

	for key in (
		"producto",
		"cantidad",
		"items",
		"items_guardados",
		"items_pendientes_clarificar",
		"modo_precio",
		"tipo_entrega",
		"metodo_pago",
		"ubicacion_entrega",
		"comprobante_file_id",
		"stock_disponible",
		"delivery_admin_notified",
		"esperando_comprobante",
		"_ya_saludo_bienvenida",
	):
		context.user_data.pop(key, None)


def _markup_modo_precio() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[
				InlineKeyboardButton("Al detal", callback_data="pedido:modo:detal"),
				InlineKeyboardButton("Al mayor", callback_data="pedido:modo:mayor"),
			],
		]
	)


def _markup_tipo_entrega() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[
				InlineKeyboardButton("Delivery", callback_data="pedido:entrega:delivery"),
				InlineKeyboardButton("Pickup", callback_data="pedido:entrega:pickup"),
			],
		]
	)


def _markup_metodo_pago() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[
				InlineKeyboardButton("Efectivo", callback_data="pedido:pago:efectivo"),
				InlineKeyboardButton("Pago móvil", callback_data="pedido:pago:movil"),
			],
		]
	)


def _markup_telefono_opcional() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		[[KeyboardButton("Compartir mi número", request_contact=True), KeyboardButton("Omitir")]],
		one_time_keyboard=True,
		resize_keyboard=True,
	)


def _texto_pide_catalogo(t: str) -> bool:
	"""Catálogo o consulta general de precios (sin producto concreto)."""

	tn = normalize_text(t.strip())
	if not tn:
		return False
	if tn in {
		"catalogo",
		"catalago",
		"ver catalogo",
		"productos",
		"lista",
		"precios",
		"ver precios",
		"lista de precios",
		"menu",
		"menú",
		"tarifas",
		"cuanto cuestan",
		"cuánto cuestan",
	}:
		return True
	if re.search(r"\b(ver|mostrar|quiero\s+ver|dame\s+el)\s+(el\s+)?(catalogo|catalago|menu|menú|lista)\b", tn):
		return True
	if re.search(r"\b(precios?|lista\s+de\s+precios?|cuanto\s+cuesta|cuánto\s+cuesta|tarifas?)\b", tn):
		return True
	return "catalogo" in tn and len(tn) < 45


def _extraer_consulta_disponibilidad(texto: str) -> str | None:
	"""Extrae el término consultado en preguntas tipo '¿tienen barquillas?'."""

	tn = normalize_text(texto or "")
	if not tn or len(tn) > 140:
		return None
	if re.search(r"\b(?:quiero|dame|necesito|agrega|agregar|comprar|pedido|orden)\b", tn):
		return None
	pat = re.search(
		r"\b(?:tienen|tendran|tendrán|hay|venden|manejan|disponen(?:\s+de)?|tienes)\s+(.+?)\s*$",
		tn,
	)
	if not pat:
		return None
	term = pat.group(1).strip(" ?¿!.")
	term = re.sub(r"^(?:de|del|el|la|los|las|un|una|unos|unas)\s+", "", term).strip()
	term = re.sub(r"\b(?:disponibles?|en\s+stock|ahorita|hoy|por\s+casualidad)\b", "", term).strip()
	if len(term) < 3:
		return None
	if term in {"catalogo", "menu", "productos", "precios"}:
		return None
	return term


def _texto_fuera_de_contexto(texto: str) -> bool:
	"""Detecta preguntas claramente ajenas a heladería/pedidos."""

	tn = normalize_text(texto or "")
	if not tn or len(tn) < 4:
		return False
	if re.search(r"\b(clima|politica|futbol|musica|pelicula|programar|codigo|tarea|matematica|capital de|noticias)\b", tn):
		return True
	if re.search(r"\b(?:que|quien|como|cuando|donde|por que|porque)\b", tn) and not re.search(
		r"\b(helado|helados|producto|productos|catalogo|precio|pedido|delivery|pickup|pago|barquilla|barquillas|tina|tinitas|paleta|cono|litro)\b",
		tn,
	):
		return True
	return False


def _texto_asesor_fuera_contexto() -> str:
	return (
		"No cuento con esa información desde este bot. "
		f"Si necesitas más ayuda, comunícate con un asesor al {PAGO_MOVIL_TELEFONO}."
	)


async def _responder_disponibilidad_producto(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
) -> int | None:
	"""Responde consultas de disponibilidad por nombre/etiqueta antes de confirmar carrito."""

	if context.user_data.get("esperando_comprobante") or _infer_estado_tras_volver_al_chat(
		context, update.effective_user.id
	) == CONFIRMANDO_PEDIDO:
		return None
	termino = _extraer_consulta_disponibilidad(texto)
	if not termino:
		return None
	recs = recomendar_productos_por_consulta(termino, catalog)
	if recs:
		await update.message.reply_text(
			"Sí, claro. Tenemos estas opciones disponibles:\n"
			+ "\n".join(f"- {p.get('nombre_producto', '')}" for p in recs[:10])
			+ "\n\nSi quieres pedir alguna, escríbela con la cantidad."
		)
	else:
		await update.message.reply_text(
			"No, disculpa, no tenemos eso disponible por ahora. "
			"Puedes escoger cualquiera de nuestros productos en el catálogo."
		)
	return _infer_estado_tras_volver_al_chat(context, update.effective_user.id)


def _texto_pregunta_identidad_helados_cali(texto: str) -> bool:
	"""Preguntas existenciales sobre la marca (no pedidos concretos)."""

	tn = normalize_text((texto or "").strip())
	if not tn or len(tn) > 140:
		return False
	if re.search(r"\d", tn) and re.search(
		r"\b(un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|\d+)\s+", tn
	):
		return False
	if re.search(r"\bque\s+venden\b", tn) and (
		len(tn.split()) > 10 or re.search(r"\b(de|del|con|para)\s+\w{3,}", tn)
	):
		return False
	return bool(
		re.search(r"\bquien\s+es\s+cali\b", tn)
		or re.search(r"\bquienes\s+son\b", tn)
		or re.search(r"\bque\s+venden\b", tn)
		or re.search(r"\bque\s+vende\s+cali\b", tn)
		or re.search(r"\bvenden\s+otra\s+cosa\b", tn)
		or re.search(r"\bvenden\s+algo\s+mas\b", tn)
		or re.search(r"\bvenden\s+algo\s+más\b", tn)
		or re.search(r"\bque\s+es\s+helados\s+cali\b", tn)
		or re.search(r"\b(de\s+que\s+trata|de\s+que\s+se\s+trata)\s+(ustedes|helados\s+cali)\b", tn)
	)


async def _responder_faq_identidad_helados_cali(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	tid: int,
) -> int:
	"""Responde quiénes son y reengancha al flujo comercial."""

	await update.message.reply_text(
		"Somos Helados Cali, alegramos corazones con la mejor variedad de paletas, conos, "
		"tinitas y helados de litro. Solo vendemos helados de alta calidad.\n\n"
		"¿Te gustaría ver el catálogo o prefieres hacer un pedido directo?"
	)
	if context.user_data.get("modo_precio") is None:
		return PIDIENDO_MODO_PRECIO
	return _infer_estado_tras_volver_al_chat(context, tid)


def _roots_proyecto_catalogo_visual() -> list[Path]:
	"""Raíces donde suele estar `assets/` (según cómo se arranque `python -m src.bot.main`)."""

	fp = Path(__file__).resolve()
	# .../IA_Chatbot/src/bot/handlers_conversation.py -> raíz del paquete IA_Chatbot
	raiz_paquete = fp.parent.parent.parent
	out: list[Path] = [raiz_paquete, Path.cwd()]
	cwd_ia = Path.cwd() / "IA_Chatbot"
	if cwd_ia.is_dir():
		out.append(cwd_ia)
	# Si el cwd es la carpeta src/ dentro del paquete
	src_parent = fp.parent.parent
	if src_parent.is_dir() and (src_parent / "bot").is_dir():
		out.append(src_parent.parent)
	seen: set[Path] = set()
	uniq: list[Path] = []
	for r in out:
		try:
			rp = r.resolve()
		except OSError:
			rp = r
		if rp not in seen:
			seen.add(rp)
			uniq.append(rp)
	return uniq


def _resolver_ruta_catalogo_visual() -> Path | None:
	"""Busca la imagen del catálogo en .env o en assets/ del proyecto."""

	if CATALOGO_VISUAL_PATH:
		p = Path(CATALOGO_VISUAL_PATH).expanduser()
		if p.is_file():
			return p.resolve()
	nombres_fijos = (
		"catalogo_helados_cali.png",
		"catalogo_helados_cali.jpg",
		"catalogo_helados_cali.jpg.jpeg",
		"WhatsApp Image 2026-05-02 at 6.43.08 PM.jpeg",
		"WhatsApp_Image_2026-05-02_at_6.43.08_PM.jpeg",
	)
	for root in _roots_proyecto_catalogo_visual():
		for name in nombres_fijos:
			candidate = root / "assets" / name
			if candidate.is_file():
				return candidate.resolve()
		assets_dir = root / "assets"
		if not assets_dir.is_dir():
			continue
		for pref in ("catalogo", "WhatsApp", "helados", "cali"):
			for ext in (".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG"):
				coincidencias = sorted(assets_dir.glob(f"{pref}*{ext}"))
				for hit in coincidencias:
					if hit.is_file():
						return hit.resolve()
	return None


async def _enviar_catalogo_visual_si_disponible(
	update: Update,
	*,
	modo_precio: str | None,
	encabezado: str,
) -> bool:
	"""Envía la imagen del catálogo REF (mayor/detal). Si no hay archivo, no hace nada."""

	ruta = _resolver_ruta_catalogo_visual()
	msg = update.effective_message
	if ruta is None or msg is None:
		return False
	mode_line = ""
	if modo_precio == "mayor":
		mode_line = "En la imagen: columna al mayor (mayorista)."
	elif modo_precio == "detal":
		mode_line = "En la imagen: columna al detal (menudeo)."
	else:
		mode_line = "En la imagen verás precios al mayor y al detal (REF)."
	caption = f"{encabezado.strip()}\n\n{mode_line}\n\nEscribe los productos que quieras o di cuando quieras cerrar el pedido."
	if len(caption) > 1020:
		caption = caption[:1017] + "..."
	try:
		with ruta.open("rb") as fh:
			await msg.reply_photo(photo=InputFile(fh, filename=ruta.name), caption=caption)
	except (OSError, BadRequest) as exc:
		_log.warning("No se pudo enviar la imagen del catálogo (%s): %s", ruta, exc)
		return False
	return True


def _texto_editar_ubicacion(t: str) -> bool:
	tn = normalize_text(t.strip())
	return any(
		x in tn
		for x in (
			"cambiar ubicacion",
			"editar ubicacion",
			"nueva ubicacion",
			"cambiar direccion",
			"editar direccion",
			"corregir direccion",
			"corregir ubicacion",
			"otra direccion",
			"otra ubicacion",
		)
	)


def _texto_editar_mis_datos(t: str) -> bool:
	tn = normalize_text(t.strip())
	return any(
		x in tn
		for x in (
			"editar mis datos",
			"cambiar mis datos",
			"actualizar mis datos",
			"corregir mis datos",
			"mis datos",
		)
	)


def _tokens_producto(texto: str, *, include_generic: bool = False) -> set[str]:
	"""Tokeniza un fragmento pensando en nombres de productos del catálogo."""

	tn = apply_colloquial_helado_terms(normalize_text(texto))
	tokens = {tok for tok in re.findall(r"\w+", tn) if len(tok) > 2}
	if include_generic:
		return tokens
	return {tok for tok in tokens if tok not in _GENERIC_PRODUCT_TOKENS}


def _token_cubierto_por_producto(token: str, product_tokens: set[str]) -> bool:
	if token in product_tokens:
		return True
	for prod_token in product_tokens:
		if len(token) >= 4 and len(prod_token) >= 4 and (token in prod_token or prod_token in token):
			return True
		if len(token) >= 4 and len(prod_token) >= 4 and SequenceMatcher(None, token, prod_token).ratio() >= 0.74:
			return True
	return False


def _fragmento_tiene_sobrantes_no_cubiertos(fragmento: str, producto: str) -> bool:
	"""Evita aceptar productos parciales cuando sobran palabras de sabor/presentación."""

	fragment_tokens = _tokens_producto(fragmento)
	product_tokens = _tokens_producto(producto)
	if not fragment_tokens or not product_tokens:
		return False
	return any(not _token_cubierto_por_producto(token, product_tokens) for token in fragment_tokens)


def _desglosar_termino_desconocido(termino: str) -> tuple[int, str]:
	"""Separa cantidad + texto cuando el término desconocido viene como '3 mousie'."""

	tn = apply_colloquial_helado_terms(normalize_text(termino))
	if not tn:
		return 1, ""
	match = re.match(rf"^\s*({_NUMBER_TOKEN_RE})\s+(.+?)\s*$", tn, flags=re.IGNORECASE)
	if not match:
		return 1, tn
	raw_qty = (match.group(1) or "").strip().lower()
	if raw_qty.isdigit():
		qty = int(raw_qty)
	else:
		qty = int(_NUM_WORDS.get(raw_qty, 1))
	return max(1, qty), (match.group(2) or "").strip()


def _aliases_producto_catalogo(producto: dict) -> set[str]:
	"""Devuelve alias útiles del catálogo para ranking flexible."""

	nombre = str(producto.get("nombre_producto", "")).strip()
	if not nombre:
		return set()
	aliases = set(_build_product_aliases(nombre))
	aliases |= {alias for alias in _etiqueta_aliases_from_product(producto) if len(alias) >= 3}
	out: set[str] = set()
	for alias in aliases:
		norm = apply_colloquial_helado_terms(normalize_text(alias))
		if norm:
			out.add(norm)
	return out


def _rank_catalog_candidates(
	fragmento: str,
	catalog: list[dict],
	*,
	restrict_to: list[str] | None = None,
	limit: int = 5,
) -> list[dict]:
	"""Ordena candidatos del catálogo para un texto parcial, typo o complemento."""

	query = apply_colloquial_helado_terms(normalize_text(fragmento))
	if not query or not catalog:
		return []

	query_tokens = _tokens_producto(query)
	allowed = {normalize_text(name) for name in (restrict_to or [])}
	ranked: list[dict] = []

	for producto in catalog:
		nombre = str(producto.get("nombre_producto", "")).strip()
		if not nombre:
			continue
		if allowed and normalize_text(nombre) not in allowed:
			continue

		best_score = 0.0
		best_overlap = 0
		for alias in _aliases_producto_catalogo(producto):
			alias_tokens = _tokens_producto(alias)
			overlap = len(query_tokens & alias_tokens) if query_tokens else 0
			fuzzy_full = SequenceMatcher(None, query, alias).ratio()
			fuzzy_token = max(
				(
					SequenceMatcher(None, q_tok, a_tok).ratio()
					for q_tok in (query_tokens or {query})
					for a_tok in (alias_tokens or {alias})
				),
				default=0.0,
			)
			contains = query == alias or query in alias or alias in query
			subset = bool(query_tokens) and bool(alias_tokens) and query_tokens <= alias_tokens
			score = (
				fuzzy_full * 0.55
				+ fuzzy_token * 0.25
				+ min(overlap, 3) * 0.12
				+ (0.18 if contains else 0.0)
				+ (0.15 if subset else 0.0)
			)
			if query == alias:
				score = max(score, 1.0)
			elif contains:
				score = max(score, 0.84 + min(overlap, 2) * 0.05)
			elif overlap and fuzzy_token >= 0.84:
				score = max(score, 0.76 + min(overlap, 2) * 0.05)
			elif overlap and subset:
				score = max(score, 0.74 + min(overlap, 2) * 0.05)

			best_score = max(best_score, min(score, 1.0))
			best_overlap = max(best_overlap, overlap)

		if best_score > 0:
			ranked.append({"producto": nombre, "score": best_score, "overlap": best_overlap})

	ranked.sort(key=lambda item: (item["score"], item["overlap"], -len(item["producto"])), reverse=True)
	return ranked[:limit]


def _select_unique_catalog_candidate(
	ranked: list[dict],
	*,
	strong_score: float,
	min_gap: float,
	allow_loose_single: bool = False,
) -> str | None:
	"""Elige un candidato solo cuando la ventaja es suficientemente clara."""

	if not ranked:
		return None
	best = ranked[0]
	second_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
	if best["score"] >= 0.97:
		return str(best["producto"])
	if best["score"] >= strong_score and (len(ranked) == 1 or best["score"] - second_score >= min_gap):
		return str(best["producto"])
	if best["overlap"] > 0 and best["score"] >= strong_score - 0.06 and (
		len(ranked) == 1 or best["score"] - second_score >= min_gap + 0.02
	):
		return str(best["producto"])
	if allow_loose_single and len(ranked) == 1 and best["score"] >= strong_score - 0.10:
		return str(best["producto"])
	return None


def _resolver_producto_desde_texto(
	texto: str,
	catalog: list[dict],
	*,
	candidatos: list[str] | None = None,
) -> tuple[str | None, list[str]]:
	"""Resuelve un producto desde texto libre, opcionalmente restringiendo candidatos."""

	tiene_tokens_especificos = bool(_tokens_producto(texto))
	intento = detect_intent(texto, catalog)
	producto_detectado = intento.get("entities", {}).get("product")
	if producto_detectado and (candidatos or tiene_tokens_especificos) and (not candidatos or producto_detectado in candidatos):
		if not candidatos and _fragmento_tiene_sobrantes_no_cubiertos(texto, str(producto_detectado)):
			ranked_partial = _rank_catalog_candidates(texto, catalog, restrict_to=candidatos, limit=5)
			return None, [str(item["producto"]) for item in ranked_partial[:5]]
		return str(producto_detectado), [str(producto_detectado)]

	ranked = _rank_catalog_candidates(texto, catalog, restrict_to=candidatos, limit=5)
	if not ranked:
		return None, []
	if not candidatos and not tiene_tokens_especificos:
		return None, [str(item["producto"]) for item in ranked[:5]]

	producto = _select_unique_catalog_candidate(
		ranked,
		strong_score=0.72 if candidatos else 0.84,
		min_gap=0.05 if candidatos else 0.08,
		allow_loose_single=bool(candidatos),
	)
	return producto, [str(item["producto"]) for item in ranked[:5]]


def _build_pending_unknown_items(terminos: list[str], catalog: list[dict]) -> tuple[list[dict], list[dict]]:
	"""Convierte términos fuera de catálogo en items auto-resueltos o pendientes."""

	resueltos: list[dict] = []
	pendientes: list[dict] = []
	seen: set[tuple[int, str]] = set()

	for termino in terminos:
		cantidad, fragmento = _desglosar_termino_desconocido(termino)
		if not fragmento:
			continue
		key = (cantidad, fragmento)
		if key in seen:
			continue
		seen.add(key)
		ranked = _rank_catalog_candidates(fragmento, catalog, limit=5)
		producto = _select_unique_catalog_candidate(
			ranked,
			strong_score=0.86,
			min_gap=0.08,
			allow_loose_single=False,
		)
		if producto:
			resueltos.append({"producto": producto, "cantidad": cantidad})
			continue
		pendientes.append(
			{
				"tipo": "desconocido",
				"segmento": fragmento,
				"cantidad": cantidad,
				"candidatos": [str(item["producto"]) for item in ranked[:5]],
			}
		)

	return resueltos, pendientes


def _split_order_segments(raw_text: str) -> list[str]:
	"""Parte un pedido compuesto en segmentos independientes."""

	if not raw_text:
		return []
	base_segments = [
		segment.strip()
		for segment in re.split(
			r"[\n,;\+]+|\s+(?:y|e)\s+|\s+ademas\s+|\s+tambien\s+|\s+mas\s+",
			raw_text,
			flags=re.IGNORECASE,
		)
		if segment.strip()
	]
	segments: list[str] = []
	qty_boundary = re.compile(rf"(?<!\w)({_NUMBER_TOKEN_RE})\s+", re.IGNORECASE)
	for segment in base_segments:
		matches_all = list(qty_boundary.finditer(segment))
		matches = []
		for idx, match in enumerate(matches_all):
			if idx > 0:
				next_word = re.match(r"([\w]+)", segment[match.end() :].strip(), flags=re.IGNORECASE)
				if next_word and normalize_text(next_word.group(1)) in {"litro", "litros", "l", "ml", "cc"}:
					continue
			matches.append(match)
		if len(matches) <= 1:
			segments.append(segment)
			continue
		for idx, match in enumerate(matches):
			start = match.start()
			end = matches[idx + 1].start() if idx + 1 < len(matches) else len(segment)
			chunk = segment[start:end].strip(" ,;")
			if chunk:
				segments.append(chunk)
	return segments


def _strip_order_prefix(segmento: str) -> str:
	"""Quita prefijos típicos de pedido para quedarnos con el posible nombre."""

	s = apply_colloquial_helado_terms(normalize_text(segmento))
	if not s:
		return ""
	while True:
		trimmed = re.sub(
			r"^(?:quiero|dame|necesito|comprar|encargar|agrega|agregar|anexa|anexar|pedido|orden)\b\s*",
			"",
			s,
			flags=re.IGNORECASE,
		).strip()
		if trimmed == s:
			return s
		s = trimmed


def _parse_segment_qty_and_name(segmento: str) -> tuple[int, str]:
	"""Extrae cantidad y nombre base de un segmento de pedido."""

	base = _strip_order_prefix(segmento)
	if not base:
		return 1, ""
	match = re.match(rf"^\s*({_NUMBER_TOKEN_RE})\s+(.+?)\s*$", base, flags=re.IGNORECASE)
	if not match:
		return 1, base
	raw_qty = (match.group(1) or "").strip().lower()
	if raw_qty.isdigit():
		qty = int(raw_qty)
	else:
		qty = int(_NUM_WORDS.get(raw_qty, 1))
	return max(1, qty), (match.group(2) or "").strip()


def _segmento_parece_producto_pendiente(segmento: str, catalog: list[dict]) -> bool:
	"""Decide si un segmento parece un producto no reconocido y no una respuesta casual."""

	_, fragmento = _parse_segment_qty_and_name(segmento)
	tn = normalize_text(fragmento)
	if not tn:
		return False
	if tn in _NON_PRODUCT_REPLY_PHRASES:
		return False
	t_entrega, m_pago = _extract_delivery_and_payment(segmento)
	if t_entrega or m_pago or _extract_location_from_text(segmento):
		return False
	intent = detect_intent(segmento, catalog).get("intent")
	if intent in {"support", "status", "cancel", "catalog", "help", "price"}:
		return False
	tokens = [tok for tok in re.findall(r"\w+", tn) if len(tok) >= 3]
	return bool(tokens)


def _fragmento_parece_producto_unico_incompleto(fragmento: str) -> bool:
	"""Detecta frases como 'helado de choco manted' como un solo producto incompleto."""

	tn = apply_colloquial_helado_terms(normalize_text(fragmento))
	if not tn:
		return False
	tokens = [tok for tok in re.findall(r"\w+", tn) if len(tok) >= 3]
	if not tokens:
		return False
	has_generic = any(tok in _GENERIC_PRODUCT_TOKENS for tok in tokens)
	specific = [
		tok
		for tok in tokens
		if tok not in _GENERIC_PRODUCT_TOKENS
		and tok not in {"sabor", "sabores", "con", "sin", "tipo"}
	]
	return has_generic and bool(specific)


def _texto_quiere_cerrar_carrito(texto: str) -> bool:
	"""Detecta cuando el cliente ya no quiere agregar más productos."""

	tn = normalize_text(texto)
	if not tn:
		return False
	if tn in {"confirmar", "listo", "continuar", "siguiente", "cerrar", "finalizar", "finaliza", "terminar", "termina"}:
		return True
	if tn in _CART_FINISH_PHRASES:
		return True
	return any(frase in tn for frase in _CART_FINISH_PHRASES)


def _texto_pregunta_mas_productos(context: ContextTypes.DEFAULT_TYPE) -> str:
	"""Mensaje corto para mantener al cliente en la etapa de carrito."""

	items = _items_desde_context(context)
	if not items:
		return "Dime qué productos quieres pedir."
	return (
		"Perfecto, ya lo agregué. Si quieres otro producto, escríbelo ahora. "
		"También puedes quitar o cambiar algo antes de confirmar. "
		"Cuando ya no quieras más, escribe por ejemplo: **eso es todo**, **quiero ordenar** o **confirmar**.\n\n"
		"Importante: después de confirmar el pedido ya no se podrán cambiar productos, cantidades ni datos. "
		"Si luego quieres cambiar algo, tendrás que cancelar y hacer un pedido nuevo."
	)


def _texto_confirmacion_final_sin_edicion() -> str:
	return (
		"Responde **sí** para confirmar y registrar el pedido, o **no** para cancelarlo.\n"
		"Importante: después de confirmar ya no se podrán cambiar productos, cantidades ni datos del pedido. "
		"Si luego quieres cambiar algo, tendrás que cancelar y hacer un pedido nuevo."
	)


def _texto_edicion_bloqueada_tras_confirmar() -> str:
	return (
		"Ya estás en la confirmación final del pedido. En esta etapa no puedo editar productos, cantidades ni datos.\n"
		"Responde **sí** para registrarlo tal como está, o **no** para cancelarlo y hacer un pedido nuevo."
	)


def _texto_quiere_editar_pedido(texto: str) -> bool:
	"""Detecta cuando el cliente quiere editar el carrito desde cualquier estado."""

	tn = normalize_text(texto)
	if not tn:
		return False
	if tn in {"editar", "modificar", "cambiar"}:
		return True
	return (
		any(p in tn for p in ("editar", "modificar", "cambiar", "corregir", "ajustar"))
		and any(p in tn for p in ("pedido", "orden", "carrito", "productos", "producto"))
	)


def _texto_afirmativo(texto: str) -> bool:
	"""Reconoce confirmaciones naturales con tolerancia a typos cortos."""

	tn = normalize_text(texto)
	if not tn:
		return False
	if tn in {"si", "s", "sip", "sii", "sis", "claro", "correcto", "confirmo", "confirmar", "dale", "ok", "okay"}:
		return True
	if re.search(r"\b(si|confirmo|confirmar|correcto|claro|dale|ok|okay|proced[eé]|listo)\b", tn):
		return not re.search(r"\b(no|negativo|cancel|cancela|cancelar)\b", tn)
	return _frase_clave_en_texto(tn, ("si", "confirmar", "correcto"), threshold=0.88)


def _texto_negativo(texto: str) -> bool:
	"""Reconoce rechazos/cancelaciones cortas sin confundir cambios como 'no al mayor'."""

	tn = normalize_text(texto)
	if not tn:
		return False
	if tn in {"no", "n", "nop", "nope", "negativo", "mejor no", "no gracias", "cancelalo", "cancelar"}:
		return True
	if re.search(r"\b(no gracias|mejor no|negativo|cancelalo|cancela(?:r)?)\b", tn):
		return True
	return False


def _texto_tiene_items_o_producto_pedido(texto: str, catalog: list[dict]) -> bool:
	"""Detecta si el mensaje trae productos/cantidades para no tratarlo como solo slot."""

	item_text = strip_control_commands_for_product_search(texto or "")
	if not item_text or not catalog:
		return False
	items, ambiguos = _extract_items_from_text(item_text, catalog)
	items_auto, pending_unknowns = _build_pending_unknown_items_from_text(item_text, catalog)
	if items or ambiguos or items_auto or pending_unknowns:
		return True
	intento = detect_intent(item_text, catalog)
	entities = intento.get("entities", {})
	if entities.get("product") or entities.get("product_clarify"):
		return True
	if entities.get("quantity") is not None and any(cue in normalize_text(item_text) for cue in _ORDER_CUES):
		return True
	return False


def _texto_tiene_slots_post_carrito(texto: str) -> bool:
	"""Detecta si el mensaje ya trae datos de entrega/pago/ubicación."""

	t_entrega, m_pago = _extract_delivery_and_payment(texto)
	return bool(t_entrega or m_pago or _extract_location_from_text(texto))


_DELIVERY_KEYWORDS = (
	"delivery",
	"domicilio",
	"a domicilio",
	"envio",
	"envio a domicilio",
	"envio a casa",
	"entrega a domicilio",
	"entregar en",
	"entregalo en",
	"mandamelo",
	"me lo mandas",
	"llevalo a",
	"delvery",
	"delivry",
)

_PICKUP_KEYWORDS = (
	"pickup",
	"pick up",
	"pick-up",
	"retiro",
	"retirar",
	"recoger",
	"recoger en tienda",
	"voy a buscar",
	"voy a buscarlo",
	"para buscar",
	"para pasar buscando",
	"paso a buscar",
	"paso por",
	"paso por tienda",
	"voy a retirar",
	"retiro en tienda",
	"buscar en tienda",
	"lo recojo",
	"lo busco",
)

_PAGO_MOVIL_KEYWORDS = (
	"pago movil",
	"pago móvil",
	"pagomovil",
	"pago movi",
)

_MAYOR_KEYWORDS = (
	"mayor",
	"al mayor",
	"precio mayor",
	"mayorista",
)

_DETAL_KEYWORDS = (
	"detal",
	"al detal",
	"menudeo",
	"detalle",
)


def _frase_clave_en_texto(texto: str, frases: tuple[str, ...], *, threshold: float = 0.86) -> bool:
	"""Detecta frases clave con tolerancia leve a errores ortográficos."""

	normalizado = normalize_text(texto)
	if not normalizado:
		return False
	tokens = re.findall(r"\w+", normalizado)
	if not tokens:
		return False

	for frase in frases:
		objetivo = normalize_text(frase)
		if not objetivo:
			continue
		if re.search(rf"(?<!\w){re.escape(objetivo)}(?!\w)", normalizado):
			return True

		objetivo_tokens = re.findall(r"\w+", objetivo)
		if not objetivo_tokens:
			continue

		window_sizes = {len(objetivo_tokens)}
		if len(objetivo_tokens) > 1:
			window_sizes.add(len(objetivo_tokens) - 1)
			window_sizes.add(len(objetivo_tokens) + 1)

		for size in sorted(window_sizes):
			if size <= 0 or size > len(tokens):
				continue
			for idx in range(len(tokens) - size + 1):
				chunk_tokens = tokens[idx : idx + size]
				chunk = " ".join(chunk_tokens)
				if SequenceMatcher(None, chunk, objetivo).ratio() >= threshold:
					return True
				if len(chunk_tokens) == len(objetivo_tokens):
					promedio = sum(
						SequenceMatcher(None, token, ref).ratio()
						for token, ref in zip(chunk_tokens, objetivo_tokens)
					) / len(objetivo_tokens)
					if promedio >= threshold:
						return True
	return False


def _modo_precio_desde_texto(texto: str) -> str | None:
	"""Infere modo de precio cuando el usuario lo menciona explícitamente."""

	tn = normalize_text(texto)
	if re.search(r"\b(al\s+)?mayor|precio\s+mayor|mayorista\b", tn) or _frase_clave_en_texto(
		tn, _MAYOR_KEYWORDS
	):
		return "mayor"
	if (
		re.search(r"\b(detal|al detal|menudeo)\b", tn)
		or ("detalle" in tn and "mayor" not in tn)
		or (_frase_clave_en_texto(tn, _DETAL_KEYWORDS) and not _frase_clave_en_texto(tn, _MAYOR_KEYWORDS))
	):
		return "detal"
	return None


def _guardar_slots_desde_texto(
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	*,
	ubicacion_permitida: bool = False,
) -> None:
	"""Guarda en memoria los slots explícitos que vengan en el mensaje actual."""

	t_entrega, m_pago = _extract_delivery_and_payment(texto)
	loc = _extract_location_from_text(texto)
	modo = _modo_precio_desde_texto(texto)

	if context.user_data.get("modo_precio") is None and modo in {"detal", "mayor"}:
		context.user_data["modo_precio"] = modo

	if t_entrega in {"delivery", "pickup"}:
		context.user_data["tipo_entrega"] = t_entrega
		if t_entrega == "pickup":
			context.user_data.setdefault("metodo_pago", "presencial")
			context.user_data["ubicacion_entrega"] = None
			context.user_data.pop("comprobante_file_id", None)
		elif context.user_data.get("metodo_pago") == "presencial":
			context.user_data["metodo_pago"] = None

	if m_pago in {"efectivo", "pago movil"}:
		context.user_data["metodo_pago"] = m_pago

	if loc and ubicacion_permitida:
		context.user_data["ubicacion_entrega"] = loc


async def _priorizar_estado_esperado(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
) -> int | None:
	"""Si el flujo ya espera un slot concreto, intentarlo antes de leer productos."""

	tid = update.effective_user.id
	estado = _infer_estado_tras_volver_al_chat(context, tid)
	catalog = obtener_catalogo_disponible()
	if _texto_tiene_items_o_producto_pedido(texto, catalog):
		return None
	t_entrega, m_pago = _extract_delivery_and_payment(texto)
	loc = _extract_location_from_text(texto)
	_guardar_slots_desde_texto(
		context,
		texto,
		ubicacion_permitida=bool(loc) and (context.user_data.get("tipo_entrega") == "delivery" or t_entrega == "delivery"),
	)

	if estado == PIDIENDO_TIPO_ENTREGA and t_entrega in {"delivery", "pickup"}:
		return await _continuar_pedido_tras_carrito(update, context)

	if estado == PIDIENDO_METODO_PAGO and m_pago in {"efectivo", "pago movil"}:
		return await _continuar_pedido_tras_carrito(update, context)

	return None


def _resolver_producto_en_carrito(
	texto: str,
	context: ContextTypes.DEFAULT_TYPE,
	catalog: list[dict],
) -> tuple[str | None, list[str]]:
	"""Resuelve un producto restringido a lo que ya existe en el carrito."""

	candidatos = [str(item["producto"]) for item in _items_desde_context(context)]
	if not candidatos:
		return None, []
	producto, sugerencias = _resolver_producto_desde_texto(texto, catalog, candidatos=candidatos)
	if producto:
		return producto, sugerencias
	tn = normalize_text(texto)
	for candidato in candidatos:
		nc = normalize_text(candidato)
		if tn == nc or (tn and tn in nc) or (nc and nc in tn):
			return candidato, [candidato]
	return None, sugerencias or candidatos[:5]


def _parse_qty_name_detail(segmento: str) -> tuple[int, str, bool]:
	"""Extrae cantidad/nombre e indica si la cantidad fue explícita."""

	base = _strip_order_prefix(segmento)
	if not base:
		return 1, "", False
	match = re.match(rf"^\s*({_NUMBER_TOKEN_RE})\s+(.+?)\s*$", base, flags=re.IGNORECASE)
	if not match:
		return 1, base, False
	raw_qty = (match.group(1) or "").strip().lower()
	if raw_qty.isdigit():
		qty = int(raw_qty)
	else:
		qty = int(_NUM_WORDS.get(raw_qty, 1))
	return max(1, qty), (match.group(2) or "").strip(), True


async def _procesar_edicion_carrito(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
) -> int | None:
	"""Aplica cambios explícitos al carrito: cambiar, agregar, quitar, ajustar cantidades."""

	items_actuales = _consolidar_items_simple(_items_desde_context(context))
	if not items_actuales:
		return None

	instrucciones = split_instructions(texto) or [texto]
	carrito: dict[str, int] = {str(item["producto"]): int(item["cantidad"]) for item in items_actuales}
	hizo_cambios = False

	for instruccion in instrucciones:
		ins = (instruccion or "").strip()
		if not ins:
			continue

		reemplazo = re.match(
			r"(?i)^(?:quiero\s+)?(?:cambiar|cambia|reemplaza|reemplazar|sustituye|sustituir)\s+(.+?)\s+por\s+(.+)$",
			ins,
		)
		if reemplazo:
			q_old, old_raw, old_exp = _parse_qty_name_detail(reemplazo.group(1))
			q_new, new_raw, new_exp = _parse_qty_name_detail(reemplazo.group(2))
			old_producto, old_sugs = _resolver_producto_en_carrito(old_raw, context, catalog)
			if not old_producto:
				await update.message.reply_text(
					"No ubico cuál producto de tu carrito quieres cambiar.\n"
					+ ("\n".join(f"- {x}" for x in old_sugs[:5]) if old_sugs else _texto_ayuda_editar_pedido(context))
				)
				return PIDIENDO_PRODUCTO
			new_producto, new_sugs = _resolver_producto_desde_texto(new_raw, catalog, candidatos=None)
			if not new_producto:
				item_tmp = {
					"tipo": "desconocido",
					"segmento": new_raw,
					"cantidad": q_new if new_exp else q_old,
					"candidatos": new_sugs[:5],
				}
				context.user_data["items_pendientes_clarificar"] = [item_tmp]
				await update.message.reply_text(_formatear_item_ambiguo(item_tmp, 1, 1))
				return PIDIENDO_PRODUCTO

			cantidad_origen = carrito.get(old_producto, 0)
			cantidad_mover = min(cantidad_origen, q_old if old_exp else cantidad_origen)
			if cantidad_mover <= 0:
				await update.message.reply_text(f"No tenías '{old_producto}' en el pedido.")
				return PIDIENDO_PRODUCTO
			carrito[old_producto] = cantidad_origen - cantidad_mover
			if carrito[old_producto] <= 0:
				carrito.pop(old_producto, None)
			carrito[new_producto] = carrito.get(new_producto, 0) + (q_new if new_exp else cantidad_mover)
			hizo_cambios = True
			continue

		ajuste = re.match(
			r"(?i)^(?:quiero\s+)?(?:poner|pon|deja|ajusta|ajustar|cambia|cambiar)\s+(.+?)\s+(?:a|en)\s+(\d+)\s*$",
			ins,
		)
		if ajuste:
			prod_raw = ajuste.group(1)
			nueva_cantidad = int(ajuste.group(2))
			producto, sugs = _resolver_producto_en_carrito(prod_raw, context, catalog)
			if not producto:
				await update.message.reply_text(
					"No ubico cuál producto de tu carrito quieres ajustar.\n"
					+ ("\n".join(f"- {x}" for x in sugs[:5]) if sugs else _texto_ayuda_editar_pedido(context))
				)
				return PIDIENDO_PRODUCTO
			if nueva_cantidad <= 0:
				carrito.pop(producto, None)
			else:
				carrito[producto] = nueva_cantidad
			hizo_cambios = True
			continue

		quitar = re.match(r"(?i)^(?:quiero\s+)?(?:quitar|eliminar|sacar|borrar|resta|restar)\s+(.+)$", ins)
		if quitar:
			qty_remove, prod_raw, qty_exp = _parse_qty_name_detail(quitar.group(1))
			producto, sugs = _resolver_producto_en_carrito(prod_raw, context, catalog)
			if not producto:
				await update.message.reply_text(
					"No ubico cuál producto de tu carrito quieres quitar.\n"
					+ ("\n".join(f"- {x}" for x in sugs[:5]) if sugs else _texto_ayuda_editar_pedido(context))
				)
				return PIDIENDO_PRODUCTO
			if qty_exp:
				carrito[producto] = max(0, carrito.get(producto, 0) - qty_remove)
				if carrito[producto] <= 0:
					carrito.pop(producto, None)
			else:
				carrito.pop(producto, None)
			hizo_cambios = True
			continue

		agregar = re.match(r"(?i)^(?:quiero\s+)?(?:agrega|agregar|anade|añade|suma|sumar|mete)\s+(.+)$", ins)
		if agregar:
			add_text = agregar.group(1).strip()
			add_items, add_ambiguos = _extract_items_from_text(add_text, catalog)
			add_auto, add_pending = _build_pending_unknown_items_from_text(add_text, catalog)
			for item in [*add_items, *add_auto]:
				carrito[item["producto"]] = carrito.get(item["producto"], 0) + int(item["cantidad"])
			if add_ambiguos or add_pending:
				context.user_data["items_pendientes_clarificar"] = [*add_ambiguos, *add_pending]
				await _guardar_items_en_context(
					update,
					context,
					[{"producto": p, "cantidad": q} for p, q in carrito.items()],
				)
				await update.message.reply_text(
					_formatear_item_ambiguo(context.user_data["items_pendientes_clarificar"][0], 1, len(context.user_data["items_pendientes_clarificar"]))
				)
				return PIDIENDO_PRODUCTO
			if add_items or add_auto:
				hizo_cambios = True
				continue

	# Pedido natural de reemplazo/agregado dentro de edición abierta.
	if not hizo_cambios:
		return None

	items_finales = [{"producto": p, "cantidad": q} for p, q in carrito.items() if q > 0]
	if not items_finales:
		context.user_data["items"] = []
		context.user_data.pop("items_guardados", None)
		context.user_data.pop("producto", None)
		context.user_data.pop("cantidad", None)
		context.user_data.pop("stock_disponible", None)
		await update.message.reply_text("Tu carrito quedó vacío. Dime qué productos quieres pedir.")
		return PIDIENDO_PRODUCTO

	try:
		await _guardar_items_en_context(update, context, items_finales)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return PIDIENDO_PRODUCTO

	if _texto_quiere_cerrar_carrito(texto) or _texto_tiene_slots_post_carrito(texto):
		return await _continuar_pedido_tras_carrito(update, context)

	await update.message.reply_text("Listo, actualicé tu pedido.\n\n" + _texto_pregunta_mas_productos(context))
	return PIDIENDO_PRODUCTO


def _build_pending_unknown_items_from_text(text: str, catalog: list[dict]) -> tuple[list[dict], list[dict]]:
	"""Crea pendientes desconocidos por producto/segmento, no por palabra suelta."""

	if not text or not catalog:
		return [], []

	resueltos: list[dict] = []
	pendientes: list[dict] = []
	seen: set[tuple[int, str]] = set()

	for segmento in _split_order_segments(text) or [text]:
		seg_items, seg_ambiguos = _extract_items_from_text(segmento, catalog)
		cantidad, fragmento = _parse_segment_qty_and_name(segmento)
		_, _, cantidad_explicita = _parse_qty_name_detail(segmento)

		seg_unknowns = list_unknown_product_terms(segmento, catalog)
		terminos_a_procesar: list[str] = []
		if seg_unknowns:
			terminos_a_procesar = seg_unknowns
		elif seg_items or seg_ambiguos:
			continue
		elif _segmento_parece_producto_pendiente(segmento, catalog) and fragmento:
			terminos_a_procesar = [f"{cantidad} {fragmento}" if cantidad_explicita else fragmento]
		else:
			continue

		for termino in terminos_a_procesar:
			term_qty, term_fragmento = _desglosar_termino_desconocido(termino)
			if not term_fragmento:
				continue
			if cantidad_explicita and len(terminos_a_procesar) == 1 and term_qty == 1:
				term_qty = cantidad
			if not cantidad_explicita and len(seg_unknowns) > 1 and term_qty == 1:
				term_qty = 1

			key = (term_qty, term_fragmento)
			if key in seen:
				continue
			seen.add(key)

			ranked = _rank_catalog_candidates(term_fragmento, catalog, limit=5)
			producto = _select_unique_catalog_candidate(
				ranked,
				strong_score=0.86,
				min_gap=0.08,
			allow_loose_single=False,
			)
			if producto:
				resueltos.append({"producto": producto, "cantidad": term_qty})
				continue

			pendientes.append(
				{
					"tipo": "desconocido",
					"segmento": term_fragmento,
					"cantidad": term_qty,
					"candidatos": [str(item["producto"]) for item in ranked[:5]],
				}
			)

	return resueltos, pendientes


def _reset_cotizacion_delivery(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> None:
	context.user_data["delivery_admin_notified"] = False
	context.user_data.pop("ubicacion_entrega", None)
	limpiar_costo_delivery_pendiente(telegram_id)


def _infer_estado_tras_volver_al_chat(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> int:
	if context.user_data.get("modo_precio") is None:
		return PIDIENDO_MODO_PRECIO
	if context.user_data.get("esperando_comprobante"):
		return PIDIENDO_COMPROBANTE
	if context.user_data.get("items_pendientes_clarificar"):
		return PIDIENDO_PRODUCTO
	items_ok = _items_desde_context(context)
	if not items_ok and not context.user_data.get("producto"):
		return PIDIENDO_PRODUCTO
	if context.user_data.get("producto") is not None and context.user_data.get("cantidad") is None:
		return PIDIENDO_CANTIDAD
	te = context.user_data.get("tipo_entrega")
	if not te:
		return PIDIENDO_TIPO_ENTREGA
	if te == "delivery":
		if not context.user_data.get("ubicacion_entrega"):
			return PIDIENDO_UBICACION
		quote = obtener_costo_delivery_pendiente(telegram_id)
		if quote is None:
			return ESPERANDO_COSTO_DELIVERY
		if not context.user_data.get("metodo_pago"):
			return PIDIENDO_METODO_PAGO
	return CONFIRMANDO_PEDIDO


def _pedido_en_curso(context: ContextTypes.DEFAULT_TYPE) -> bool:
	return context.user_data.get("modo_precio") is not None


def _pedido_tiene_items(context: ContextTypes.DEFAULT_TYPE) -> bool:
	"""Valida si hay contenido mínimo del pedido para construir el resumen."""

	items = context.user_data.get("items_guardados") or context.user_data.get("items")
	if items:
		return True
	producto = context.user_data.get("producto")
	cantidad = context.user_data.get("cantidad")
	return bool(producto) and cantidad is not None


def _ubicacion_a_texto(latitud: float, longitud: float) -> str:
	"""Convierte coordenadas a formato interno para persistirlas en DB."""

	return f"geo:{latitud:.6f},{longitud:.6f}"


def _parsear_geo(ubicacion_entrega: str | None) -> tuple[float, float] | None:
	"""Parsea formato geo:lat,lon cuando la ubicación viene de Telegram."""

	if not ubicacion_entrega or not ubicacion_entrega.startswith("geo:"):
		return None

	payload = ubicacion_entrega[4:]
	partes = payload.split(",", maxsplit=1)
	if len(partes) != 2:
		return None

	try:
		lat = float(partes[0])
		lon = float(partes[1])
	except ValueError:
		return None

	return lat, lon


def _ubicacion_legible(ubicacion_entrega: str | None) -> str:
	"""Devuelve una versión legible de la ubicación guardada."""

	geo = _parsear_geo(ubicacion_entrega)
	if geo is not None:
		lat, lon = geo
		return f"Lat {lat:.5f}, Lon {lon:.5f}"
	return ubicacion_entrega or "No indicada"


def _texto_admin_entrega_delivery(
	tipo_entrega: str | None,
	ubicacion_entrega: str | None,
	delivery_costo_usd: float,
) -> tuple[str, str]:
	"""Texto para aviso admin: (detalle ubicación/costo, pie de instrucciones). Evita 'pendiente' si ya hay datos."""

	if tipo_entrega != "delivery":
		return ("", "Gestiona el pedido con los botones o comandos /admin_*\n")

	tiene_ubic = bool((ubicacion_entrega or "").strip())
	tiene_delivery = float(delivery_costo_usd or 0) > 0
	detalle: list[str] = []
	if tiene_ubic:
		detalle.append(f"Ubicación: {_ubicacion_legible(ubicacion_entrega)}")
	else:
		detalle.append("Ubicación: aún no registrada en el pedido")

	if tiene_delivery:
		detalle.append(f"Costo delivery: ${float(delivery_costo_usd):.2f}")

	if tiene_ubic and tiene_delivery:
		pie = (
			"Ubicación y delivery ya están en el pedido. Revisa el comprobante (foto siguiente, si aplica) "
			"y gestiona con los botones o comandos /admin_*.\n"
		)
	elif tiene_ubic and not tiene_delivery:
		pie = (
			"Responde con el monto de delivery en este chat si aún no lo asignaste. "
			"Luego gestiona con los botones o comandos /admin_*.\n"
		)
	else:
		pie = (
			"Revisa la ubicación y responde con el monto de delivery en este chat. "
			"Luego usa los botones o comandos /admin_*.\n"
		)
	return ("\n".join(detalle) + "\n", pie)


def _texto_pedir_modo_precio() -> str:
	u = obtener_umbral_precio_mayor_usd()
	return (
		"Primero elige cómo compras (antes del catálogo y del pedido):\n\n"
		"• detal — catálogo solo con precio al detal.\n"
		f"• mayor — catálogo solo con precio al mayor. Tu pedido debe sumar al menos ${u:.2f} USD "
		"a ese precio (umbral definido por la tienda). Si no llegas, agrega más productos.\n\n"
		"Responde por texto o con los botones: detal o mayor."
	)


async def _enviar_pregunta_modo_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Envía la pregunta detal/mayor con teclado inline."""

	msg = update.effective_message
	if not msg or not update.effective_user:
		return
	lineas: list[str] = []
	if not context.user_data.get("_ya_saludo_bienvenida"):
		context.user_data["_ya_saludo_bienvenida"] = True
		urow = obtener_usuario_por_telegram_id(update.effective_user.id)
		nombre = nombre_publico_usuario(urow) if urow else ""
		if nombre:
			lineas.append(f"¡Hola, {nombre}! Bienvenido al asistente de pedidos de helados.")
		else:
			lineas.append("¡Hola! Bienvenido al asistente de pedidos de helados.")
	lineas.append(_texto_pedir_modo_precio())
	await msg.reply_text("\n\n".join(lineas), reply_markup=_markup_modo_precio())


async def _aplicar_modo_precio_y_catalogo(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	modo: str,
) -> int:
	"""Guarda modo, envía catálogo y deja listo el paso de productos."""

	context.user_data["modo_precio"] = modo
	productos = obtener_catalogo_disponible()
	if not productos:
		msg = update.effective_message
		if msg:
			await msg.reply_text("No hay productos disponibles en este momento.")
		return ConversationHandler.END

	u = obtener_umbral_precio_mayor_usd()
	if modo == "mayor":
		titulo = f"Productos (precio al mayor). Mínimo de pedido a este precio: ${u:.2f} USD."
	else:
		titulo = "Productos (precio al detal)."
	msg = update.effective_message
	if msg:
		await _enviar_catalogo(update, titulo, (productos, modo))
		await msg.reply_text(
			"Escribe qué quieres llevar y las cantidades. Ejemplo: quiero 8 helados. "
			"Puedes escribir editar en cualquier momento para cambiar el pedido."
		)
	return PIDIENDO_PRODUCTO


def _items_desde_context(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
	items = context.user_data.get("items")
	if items:
		return [{"producto": str(i["producto"]), "cantidad": int(i["cantidad"])} for i in items]
	guardados = context.user_data.get("items_guardados")
	if guardados:
		return [{"producto": str(i["producto"]), "cantidad": int(i["cantidad"])} for i in guardados]
	p = context.user_data.get("producto")
	q = context.user_data.get("cantidad")
	if p and q is not None:
		return [{"producto": str(p), "cantidad": int(q)}]
	return []


def _consolidar_items_simple(items: list[dict]) -> list[dict]:
	"""Agrupa items repetidos por nombre sin perder cantidades."""

	merged: dict[str, int] = {}
	for item in items:
		producto = str(item.get("producto", "")).strip()
		cantidad = int(item.get("cantidad", 0) or 0)
		if not producto or cantidad <= 0:
			continue
		merged[producto] = merged.get(producto, 0) + cantidad
	return [{"producto": producto, "cantidad": cantidad} for producto, cantidad in merged.items()]


async def _guardar_items_en_context(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	items: list[dict],
) -> None:
	"""Persiste el carrito consolidado y limpia el fallback single-product."""

	items_norm = _consolidar_items_simple(items)
	context.user_data["items"] = items_norm
	context.user_data["items_guardados"] = _preparar_items_guardados(
		items_norm, context.user_data.get("modo_precio")
	)
	context.user_data.pop("producto", None)
	context.user_data.pop("cantidad", None)
	context.user_data.pop("stock_disponible", None)


async def _sumar_items_al_carrito(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	nuevos_items: list[dict],
) -> None:
	"""Suma nuevos ítems al carrito actual preservando slots ya recolectados."""

	stored: dict[str, int] = {}
	for it in _items_desde_context(context):
		stored[it["producto"]] = stored.get(it["producto"], 0) + int(it.get("cantidad", 1))
	for it in nuevos_items:
		producto = str(it.get("producto", "")).strip()
		cantidad = int(it.get("cantidad", 0) or 0)
		if producto and cantidad > 0:
			stored[producto] = stored.get(producto, 0) + cantidad
	await _guardar_items_en_context(
		update,
		context,
		[{"producto": producto, "cantidad": cantidad} for producto, cantidad in stored.items()],
	)


def _texto_ayuda_editar_pedido(context: ContextTypes.DEFAULT_TYPE) -> str:
	mod = context.user_data.get("modo_precio") or "detal"
	lines = [
		"Ajustemos tu pedido.",
		f"Modo: {'al mayor' if mod == 'mayor' else 'al detal'}.",
		"",
		"Carrito:",
	]
	items_simple = _items_desde_context(context)
	if not items_simple:
		lines.append("(vacío — dime qué productos quieres)")
	else:
		for it in items_simple:
			lines.append(f"- {it['producto']} x{it['cantidad']}")
	lines.extend(
		[
			"",
			"Puedes agregar cantidades en un mensaje (ej.: 3 helado chocolate y 2 paletas), "
			"quitar una línea con: quitar <producto>, y cuando termines escribe confirmar.",
		]
	)
	return "\n".join(lines)


def _build_admin_actions_markup(tipo_entrega: str, pedido_id: int) -> InlineKeyboardMarkup:
	"""Construye botones contextuales para acciones de admin por pedido."""

	if tipo_entrega == "delivery":
		buttons = [
			[
				InlineKeyboardButton(
					"Ver comprobante",
					callback_data=f"admin:comprobante:{pedido_id}",
				),
				InlineKeyboardButton(
					"Confirmar comprobante y despachar",
					callback_data=f"admin:confirmar:{pedido_id}",
				),
			],
			[
				InlineKeyboardButton(
					"Marcar entregado",
					callback_data=f"admin:entregado:{pedido_id}",
				),
			],
		]
	else:
		buttons = [
			[
				InlineKeyboardButton(
					"Concluir pedido pickup",
					callback_data=f"admin:concluir:{pedido_id}",
				),
			],
		]

	return InlineKeyboardMarkup(buttons)


def _lineas_bcv_resumen(total_usd: Decimal, tasa_bcv: Decimal, fecha_valor: str | None, bcv_origen: str | None) -> str:
	"""Formatea conversión Bs usando tasa BCV (en vivo o cache)."""

	total_bs = (total_usd * tasa_bcv).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	lineas = (
		f"\n- Tasa BCV USD: {tasa_bcv} Bs"
		f"\n- Total Bs: {total_bs} Bs"
	)
	if fecha_valor:
		lineas += f"\n- Fecha valor BCV: {fecha_valor}"
	if bcv_origen == "cache":
		lineas += "\n- Nota: BCV tardó/no respondió a tiempo; se usó la última tasa guardada en el bot."
	return lineas


def _build_resumen_pedido(context: ContextTypes.DEFAULT_TYPE) -> str:
	"""Construye un resumen del pedido en curso para mostrar al usuario."""
	tipo_entrega = context.user_data.get("tipo_entrega")
	metodo_pago = context.user_data.get("metodo_pago")
	ubicacion_entrega = context.user_data.get("ubicacion_entrega")
	telegram_id = context.user_data.get("telegram_id")

	items = context.user_data.get("items_guardados") or context.user_data.get("items")
	if items:
		# Resumen multi-item
		delivery_costo = 0
		if tipo_entrega == "delivery" and isinstance(telegram_id, int):
			costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)
			if costo_pendiente is not None:
				delivery_costo = float(costo_pendiente)

		total_usd = 0
		subtotal_lines = []
		modo_precio = context.user_data.get("modo_precio")
		for it in items:
			producto = it["producto"]
			cantidad = int(it.get("cantidad", 1))
			montos = obtener_resumen_montos(
				producto, cantidad, delivery_costo_usd=0, modo_precio=modo_precio, incluir_bcv=False
			)
			subtotal_lines.append(f"- {producto} x{cantidad}: ${montos['subtotal_usd']}")
			total_usd += float(montos["subtotal_usd"])

		if tipo_entrega == "delivery":
			if delivery_costo > 0:
				total_usd += delivery_costo
				delivery_line = f"- Costo de envío: ${delivery_costo:.2f}"
			else:
				delivery_line = _TEXTO_RESUMEN_COSTO_ENVIO_PENDIENTE
		else:
			delivery_line = ""

		lineas_bcv = ""
		try:
			# En UI (resumen) preferimos no bloquear mucho el hilo async: timeout corto + cache.
			tasa_bcv, fecha_valor, bcv_origen = obtener_tasa_usd_bcv(timeout_s=3.0)
			lineas_bcv = _lineas_bcv_resumen(Decimal(str(total_usd)), tasa_bcv, fecha_valor, bcv_origen)
		except Exception:
			lineas_bcv = "\n- Monto Bs (BCV): no disponible temporalmente"

		datos_pago_movil = ""
		if metodo_pago == "pago movil":
			datos = obtener_datos_pago_movil()
			datos_pago_movil = (
				"\n\nDatos para pago móvil:\n"
				f"- Teléfono: {datos['telefono']}\n"
				f"- Cédula: {datos['cedula']}\n"
				f"- Banco: {datos['banco']}"
			)

		mod_label = "al mayor" if context.user_data.get("modo_precio") == "mayor" else "al detal"
		return (
			"Resumen de pedido:\n"
			f"- Precios: {mod_label}\n"
			+ "\n".join(subtotal_lines)
			+ f"\n{delivery_line}\n- Total USD: ${total_usd:.2f}"
			+ f"{lineas_bcv}\n"
			+ f"- Entrega: {tipo_entrega}\n"
			+ f"- Ubicación: {_ubicacion_legible(ubicacion_entrega)}\n"
			+ f"- Pago: {metodo_pago}\n"
			+ f"{datos_pago_movil}\n\n"
			+ _texto_confirmacion_final_sin_edicion()
		)

	# Fallback al flujo single-product (compatibilidad)
	producto = context.user_data.get("producto")
	cantidad = context.user_data.get("cantidad")

	delivery_costo = 0
	if tipo_entrega == "delivery" and isinstance(telegram_id, int):
		costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)
		if costo_pendiente is not None:
			delivery_costo = float(costo_pendiente)

	modo_precio = context.user_data.get("modo_precio")
	montos = obtener_resumen_montos(
		producto, cantidad, delivery_costo_usd=delivery_costo, modo_precio=modo_precio, incluir_bcv=False
	)
	lineas_montos = f"- Subtotal USD: ${montos['subtotal_usd']}"

	if tipo_entrega == "delivery":
		if delivery_costo > 0:
			lineas_montos += f"\n- Costo de envío: ${delivery_costo:.2f}"
		else:
			lineas_montos += f"\n{_TEXTO_RESUMEN_COSTO_ENVIO_PENDIENTE}"

	lineas_montos += f"\n- Total USD: ${montos['total_usd']}"
	lineas_bcv = ""
	try:
		tasa_bcv, fecha_valor, bcv_origen = obtener_tasa_usd_bcv(timeout_s=3.0)
		lineas_bcv = _lineas_bcv_resumen(Decimal(str(montos["total_usd"])), tasa_bcv, fecha_valor, bcv_origen)
	except Exception:
		lineas_bcv = "\n- Monto Bs (BCV): no disponible temporalmente"
	lineas_montos += lineas_bcv

	datos_pago_movil = ""
	if metodo_pago == "pago movil":
		datos = obtener_datos_pago_movil()
		datos_pago_movil = (
			"\n\nDatos para pago móvil:\n"
			f"- Teléfono: {datos['telefono']}\n"
			f"- Cédula: {datos['cedula']}\n"
			f"- Banco: {datos['banco']}"
		)

	mod_label = "al mayor" if context.user_data.get("modo_precio") == "mayor" else "al detal"
	return (
		f"Resumen de pedido:\n"
		f"- Precios: {mod_label}\n"
		f"- Producto: {producto}\n"
		f"- Cantidad: {cantidad}\n"
		f"- Entrega: {tipo_entrega}\n"
		f"- Ubicación: {_ubicacion_legible(ubicacion_entrega)}\n"
		f"- Pago: {metodo_pago}\n"
		f"{lineas_montos}"
		f"{datos_pago_movil}\n\n"
		f"{_texto_confirmacion_final_sin_edicion()}"
	)


async def _build_resumen_pedido_async(context: ContextTypes.DEFAULT_TYPE) -> str:
	"""Arma el resumen en un worker thread para no bloquear el polling ni el reloj async."""

	loop = asyncio.get_running_loop()
	return await loop.run_in_executor(None, _build_resumen_pedido, context)


def _cedula_normalizada_valida(texto: str) -> tuple[bool, str]:
	d = re.sub(r"\D", "", texto or "")
	return (6 <= len(d) <= 12, d)


async def iniciar_mis_datos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Actualiza datos personales del cliente."""

	_limpiar_datos_pedido(context)
	tid = update.effective_user.id
	context.user_data["telegram_id"] = tid
	context.user_data["editando_perfil"] = True
	asegurar_usuario_telegram(tid, update.effective_user.username)
	await update.message.reply_text(
		"Actualizamos tus datos. Escribe tu **nombre completo** (puedes incluir apellidos en la misma línea).",
		reply_markup=ReplyKeyboardRemove(),
	)
	return PIDIENDO_NOMBRE_CLIENTE


async def recibir_nombre_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	tid = update.effective_user.id
	n = (update.message.text or "").strip()
	if len(n) < 2:
		await update.message.reply_text(
			"Escribe tu nombre completo (nombre y apellidos si quieres), al menos 2 caracteres."
		)
		return PIDIENDO_NOMBRE_CLIENTE
	guardar_nombre_completo_cliente(tid, n)
	# En /mis_datos siempre pedimos cédula y teléfono de nuevo aunque ya existan.
	if usuario_perfil_completo(tid) and not context.user_data.get("editando_perfil"):
		await update.message.reply_text(
			"Opcional: comparte tu número con el botón o escríbelo; si prefieres no enviarlo, pulsa Omitir.",
			reply_markup=_markup_telefono_opcional(),
		)
		return PIDIENDO_TELEFONO_CLIENTE
	await update.message.reply_text(
		"Gracias. Ahora tu **cédula** de identidad (solo números, entre 6 y 12 dígitos)."
	)
	return PIDIENDO_CEDULA_CLIENTE


async def recibir_cedula_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	tid = update.effective_user.id
	ok, ced = _cedula_normalizada_valida(update.message.text or "")
	if not ok:
		await update.message.reply_text("La cédula debe tener entre 6 y 12 dígitos. Inténtalo de nuevo.")
		return PIDIENDO_CEDULA_CLIENTE
	guardar_cedula_cliente(tid, ced)
	await update.message.reply_text(
		"Opcional: comparte tu número con el botón o escríbelo; si prefieres no enviarlo, pulsa Omitir.",
		reply_markup=_markup_telefono_opcional(),
	)
	return PIDIENDO_TELEFONO_CLIENTE


async def recibir_telefono_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	tid = update.effective_user.id
	telefono: str | None = None
	if update.message.contact and update.message.contact.phone_number:
		telefono = update.message.contact.phone_number.strip()
	else:
		raw = (update.message.text or "").strip()
		if raw.lower() in {"omitir", "no", "-", "skip"}:
			telefono = None
		else:
			digits = re.sub(r"\D", "", raw)
			if len(digits) < 10:
				await update.message.reply_text(
					"No reconocí un número. Escribe al menos 10 dígitos o pulsa Omitir."
				)
				return PIDIENDO_TELEFONO_CLIENTE
			telefono = raw.strip()

	actualizar_telefono_cliente(tid, telefono)

	editando = bool(context.user_data.pop("editando_perfil", None))
	registro_para_pedido = bool(context.user_data.pop("registro_para_pedido", None))

	await update.message.reply_text(
		"Listo, guardé tus datos. ¡Gracias!",
		reply_markup=ReplyKeyboardRemove(),
	)

	if registro_para_pedido:
		await _enviar_pregunta_modo_precio(update, context)
		return PIDIENDO_MODO_PRECIO
	if editando:
		await update.message.reply_text("Cuando quieras, usa /pedido para armar un pedido.")
		return ConversationHandler.END
	await update.message.reply_text("Cuando quieras, usa /pedido o escribe lo que necesitas.")
	return ConversationHandler.END


async def pedido_cliente_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Botones inline: modo de precio, entrega y método de pago."""

	q = update.callback_query
	if q is None or q.data is None:
		return ConversationHandler.END
	await _answer_callback_query_safe(q)
	parts = q.data.split(":")
	if len(parts) < 3 or parts[0] != "pedido":
		return ConversationHandler.END
	kind, value = parts[1], parts[2]
	context.user_data["telegram_id"] = update.effective_user.id
	if context.user_data.get("esperando_comprobante") or _infer_estado_tras_volver_al_chat(
		context, update.effective_user.id
	) == CONFIRMANDO_PEDIDO:
		await q.message.reply_text(_texto_edicion_bloqueada_tras_confirmar())
		return PIDIENDO_COMPROBANTE if context.user_data.get("esperando_comprobante") else CONFIRMANDO_PEDIDO

	if kind == "modo":
		if value not in {"detal", "mayor"}:
			return PIDIENDO_MODO_PRECIO
		return await _aplicar_modo_precio_y_catalogo(update, context, value)

	if kind == "entrega":
		if value == "delivery":
			context.user_data["tipo_entrega"] = "delivery"
			await q.message.reply_text(_MSG_SOLICITAR_UBICACION_DELIVERY)
			return PIDIENDO_UBICACION
		if value == "pickup":
			context.user_data["tipo_entrega"] = "pickup"
			context.user_data["metodo_pago"] = "presencial"
			context.user_data["ubicacion_entrega"] = None
			context.user_data["comprobante_file_id"] = None
			if not _pedido_tiene_items(context):
				await q.message.reply_text(
					"No encontré productos activos en este pedido. Escribe /pedido para iniciarlo de nuevo."
				)
				return PIDIENDO_PRODUCTO
			await q.message.reply_text(await _build_resumen_pedido_async(context))
			return CONFIRMANDO_PEDIDO
		return PIDIENDO_TIPO_ENTREGA

	if kind == "pago":
		if value == "efectivo":
			context.user_data["metodo_pago"] = "efectivo"
		elif value == "movil":
			context.user_data["metodo_pago"] = "pago movil"
		else:
			return PIDIENDO_METODO_PAGO
		context.user_data["comprobante_file_id"] = context.user_data.get("comprobante_file_id")
		if not _pedido_tiene_items(context):
			await q.message.reply_text(
				"No encontré productos activos en este pedido. Escribe /pedido para iniciarlo de nuevo."
			)
			return PIDIENDO_PRODUCTO
		await q.message.reply_text(await _build_resumen_pedido_async(context))
		return CONFIRMANDO_PEDIDO

	return ConversationHandler.END


async def iniciar_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Inicia el flujo de pedido (registro si hace falta, luego modo detal/mayor)."""

	_limpiar_datos_pedido(context)
	tid = update.effective_user.id
	context.user_data["telegram_id"] = tid
	asegurar_usuario_telegram(tid, update.effective_user.username)
	if not usuario_perfil_completo(tid):
		context.user_data["registro_para_pedido"] = True
		paso = siguiente_paso_registro_incompleto(tid)
		if paso == "cedula":
			await update.message.reply_text(
				"Antes del pedido falta tu **cédula** de identidad. Envíala solo con números (6 a 12 dígitos).",
				reply_markup=ReplyKeyboardRemove(),
			)
			return PIDIENDO_CEDULA_CLIENTE
		await update.message.reply_text(
			"Antes del pedido necesito tus datos. Escribe tu **nombre completo** "
			"(puedes incluir apellidos en la misma línea).",
			reply_markup=ReplyKeyboardRemove(),
		)
		return PIDIENDO_NOMBRE_CLIENTE

	await _enviar_pregunta_modo_precio(update, context)
	return PIDIENDO_MODO_PRECIO


async def recibir_modo_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Recibe la elección del cliente entre 'detal' o 'mayor' y continúa el flujo."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	texto_raw = update.message.text or ""
	texto = normalize_text(texto_raw)
	if re.search(r"\b(detal|detalle|detalles|al detal|menudeo)\b", texto):
		modo = "detal"
	elif re.search(r"\b(mayor|al mayor|mayorista|precio mayor)\b", texto):
		modo = "mayor"
	else:
		modo = _modo_precio_desde_texto(texto)
	if modo not in {"detal", "mayor"}:
		await update.message.reply_text(
			"Responde solo 'detal' o 'mayor'. ¿Cómo prefieres el precio?",
			reply_markup=_markup_modo_precio(),
		)
		return PIDIENDO_MODO_PRECIO

	context.user_data["modo_precio"] = modo
	catalog = obtener_catalogo_disponible()
	if _texto_tiene_items_o_producto_pedido(texto_raw, catalog):
		parsed = _parse_order_message(texto_raw, catalog, PIDIENDO_PRODUCTO)
		if parsed.items:
			try:
				await _sumar_items_al_carrito(update, context, parsed.items)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return PIDIENDO_PRODUCTO
			_aplicar_parse_slots(context, parsed)
		if parsed.ambiguos:
			context.user_data["items_pendientes_clarificar"] = parsed.ambiguos
			await update.message.reply_text(_formatear_item_ambiguo(parsed.ambiguos[0], 1, len(parsed.ambiguos)))
			return PIDIENDO_PRODUCTO
		if parsed.items:
			if _texto_tiene_slots_post_carrito(texto_raw) or parsed.has_slots:
				return await _continuar_pedido_tras_carrito(update, context)
			await update.message.reply_text(_texto_pregunta_mas_productos(context))
			return PIDIENDO_PRODUCTO

	return await _aplicar_modo_precio_y_catalogo(update, context, modo)


async def recibir_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Guarda el producto y pide la cantidad."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	producto_texto = update.message.text.strip()
	catalog = obtener_catalogo_disponible()
	slot_global = await _procesar_slots_globales_sin_items(
		update, context, producto_texto, catalog, update.effective_user.id
	)
	if slot_global is not None:
		return slot_global
	intento = detect_intent(producto_texto, catalog)
	intent = intento.get("intent")
	entities = intento.get("entities", {})
	producto_nombre = entities.get("product")
	productos_sugeridos = entities.get("product_candidates", [])
	producto_clarificar = bool(entities.get("product_clarify"))

	modo = context.user_data.get("modo_precio") or "detal"

	if intent == "catalog":
		await _enviar_catalogo(
			update,
			"Catálogo:",
			(catalog, modo),
		)
		await update.message.reply_text(
			"Si quieres pedir alguno, escríbeme el nombre del producto y la cantidad."
		)
		return PIDIENDO_PRODUCTO

	if intent == "price":
		if producto_nombre:
			producto_db = obtener_producto_disponible_por_nombre(producto_nombre)
			if producto_db is not None:
				await update.message.reply_text(_formatear_precio_producto(producto_db, modo))
				await update.message.reply_text(
					"Si quieres, también puedo ayudarte a armar el pedido."
				)
				return PIDIENDO_PRODUCTO
		await _enviar_catalogo(update, "Catálogo:", (catalog, modo))
		await update.message.reply_text("Dime cuál producto quieres consultar y te digo su precio.")
		return PIDIENDO_PRODUCTO

	if not producto_nombre:
		if producto_clarificar and productos_sugeridos:
			uk = list_unknown_product_terms(producto_texto, catalog)
			partes: list[str] = []
			if uk:
				partes.append(_mensaje_terminos_no_catalogo(uk))
			partes.append(
				"No quedó claro cuál de estas líneas del catálogo es:\n"
				+ "\n".join(f"- {c}" for c in productos_sugeridos[:8])
			)
			if not uk:
				partes.append(_texto_recordatorio_nombre_catalogo())
			await update.message.reply_text("\n\n".join(partes))
			return PIDIENDO_PRODUCTO

		if intent == "order" and productos_sugeridos:
			uk = list_unknown_product_terms(producto_texto, catalog)
			msg = "No identifiqué un producto exacto. Puedes elegir uno de estos:\n" + "\n".join(
				f"- {c}" for c in productos_sugeridos[:8]
			)
			if uk:
				msg = _mensaje_terminos_no_catalogo(uk) + "\n\n" + msg
			else:
				msg += "\n\n" + _texto_recordatorio_nombre_catalogo()
			await update.message.reply_text(msg)
		else:
			uk = list_unknown_product_terms(producto_texto, catalog)
			if uk:
				await update.message.reply_text(_mensaje_terminos_no_catalogo(uk))
			else:
				respuesta = generar_respuesta_natural(
					producto_texto,
					contexto="el cliente está hablando antes de elegir producto en una tienda de helados",
					objetivo="redacta una respuesta breve para reconducir al cliente al catálogo sin iniciar otro flujo",
				)
				await update.message.reply_text(respuesta)
		return PIDIENDO_PRODUCTO

	producto_db = obtener_producto_disponible_por_nombre(producto_nombre)
	if producto_db is None:
		uk = list_unknown_product_terms(producto_texto, catalog)
		if uk:
			await update.message.reply_text(_mensaje_terminos_no_catalogo(uk))
		else:
			await update.message.reply_text(
				"No encontré ese nombre en el catálogo con stock.\n" + _texto_recordatorio_nombre_catalogo()
			)
		return PIDIENDO_PRODUCTO

	context.user_data["producto"] = producto_db["nombre_producto"]
	context.user_data["stock_disponible"] = producto_db["cantidad"]
	ent_mq = entities or {}
	prefix_mq = ""
	if "quantity" in (ent_mq.get("missing_fields") or []):
		prefix_mq = (
			f"Me falta la **cantidad** para «{producto_db['nombre_producto']}» "
			f"(por ejemplo: 4 {producto_db['nombre_producto']}).\n\n"
		)
	await update.message.reply_text(
		prefix_mq
		+ generar_respuesta_natural(
			f"El cliente ya eligio {producto_db['nombre_producto']} pero no indico la cantidad.",
			contexto="en un chatbot de helados falta la cantidad del pedido",
			objetivo="haz una sola pregunta breve para pedir la cantidad exacta del producto elegido",
		)
	)
	return PIDIENDO_CANTIDAD


async def recibir_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Guarda la cantidad y pide confirmación."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	cantidad_texto = update.message.text.strip()
	if not es_entero_positivo(cantidad_texto):
		await update.message.reply_text(
			generar_respuesta_natural(
				f"El cliente escribio una cantidad invalida: {cantidad_texto}.",
				contexto="en un chatbot de helados se debe pedir una cantidad valida",
				objetivo="pide solo un numero entero positivo de forma breve",
			)
		)
		return PIDIENDO_CANTIDAD

	cantidad = int(cantidad_texto)
	stock_disponible = context.user_data["stock_disponible"]
	if cantidad > stock_disponible:
		await update.message.reply_text(
			generar_respuesta_natural(
				"La cantidad solicitada supera lo disponible para ese producto.",
				contexto="en un chatbot de helados se debe avisar con naturalidad cuando no hay stock suficiente",
				objetivo="indica que no hay stock suficiente y pide una cantidad menor sin dar cifras de inventario",
			)
		)
		return PIDIENDO_CANTIDAD

	context.user_data["cantidad"] = cantidad

	modo = context.user_data.get("modo_precio")
	items_simple = [{"producto": context.user_data["producto"], "cantidad": cantidad}]
	cumple, sub, umb = validar_minimo_compra_mayor(items_simple, modo)
	if not cumple:
		context.user_data["items"] = items_simple
		try:
			context.user_data["items_guardados"] = _preparar_items_guardados(items_simple, modo)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return PIDIENDO_CANTIDAD
		context.user_data.pop("producto", None)
		context.user_data.pop("cantidad", None)
		context.user_data.pop("stock_disponible", None)
		await update.message.reply_text(
			f"En modo al mayor tu pedido debe sumar al menos ${float(umb):.2f} USD a precio mayorista. "
			f"Llevas ${float(sub):.2f}. Agrega más productos o aumenta cantidades en un mensaje "
			"(ej.: 3 helado chocolate y 5 paletas). Cuando alcances el mínimo, escribe confirmar."
		)
		return PIDIENDO_PRODUCTO

	await update.message.reply_text(
		"¿El pedido será delivery o pickup?",
		reply_markup=_markup_tipo_entrega(),
	)
	return PIDIENDO_TIPO_ENTREGA


async def recibir_tipo_entrega(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Guarda tipo de entrega y deriva a método de pago según el flujo."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	tipo_entrega, _ = _extract_delivery_and_payment(update.message.text or "")
	if tipo_entrega not in {"delivery", "pickup"}:
		await update.message.reply_text(
			generar_respuesta_natural(
				f"El cliente escribio un tipo de entrega invalido: {tipo_entrega}.",
				contexto="en un chatbot de helados se debe pedir delivery o pickup de forma natural",
				objetivo="pide que responda solo con delivery o pickup",
			),
			reply_markup=_markup_tipo_entrega(),
		)
		return PIDIENDO_TIPO_ENTREGA

	context.user_data["tipo_entrega"] = tipo_entrega

	if tipo_entrega == "delivery":
		await update.message.reply_text(_MSG_SOLICITAR_UBICACION_DELIVERY)
		return PIDIENDO_UBICACION

	context.user_data["metodo_pago"] = "presencial"
	context.user_data["ubicacion_entrega"] = None
	context.user_data["comprobante_file_id"] = None
	await update.message.reply_text(await _build_resumen_pedido_async(context))
	return CONFIRMANDO_PEDIDO


async def recibir_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Recibe ubicación (mapa o texto) para delivery y notifica al admin."""

	msg = update.effective_message
	if msg is None:
		return PIDIENDO_UBICACION
	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out

	ubicacion = None
	if msg.location:
		ubicacion = _ubicacion_a_texto(msg.location.latitude, msg.location.longitude)
	elif msg.text:
		t_raw = msg.text.strip()
		catalog = obtener_catalogo_disponible()
		t_entrega, m_pago = _extract_delivery_and_payment(t_raw)
		modo_txt = _modo_precio_desde_texto(t_raw)
		loc_txt = _extract_location_from_text(t_raw)
		if (t_entrega or m_pago or modo_txt) and not loc_txt:
			if t_entrega == "delivery" and context.user_data.get("tipo_entrega") == "delivery":
				await msg.reply_text(
					"Sí, el pedido está marcado como delivery. Ahora envíame la dirección o comparte la ubicación."
				)
				return PIDIENDO_UBICACION
			await msg.reply_text("Ahora necesito la dirección o ubicación real para el delivery.")
			return PIDIENDO_UBICACION
		if _texto_tiene_items_o_producto_pedido(t_raw, catalog):
			await msg.reply_text(
				"En este paso solo necesito la dirección o ubicación del delivery. "
				"Los cambios de productos se hacen antes de confirmar el carrito."
			)
			return PIDIENDO_UBICACION
		if _texto_pide_catalogo(t_raw):
			modo = context.user_data.get("modo_precio") or "detal"
			cat = catalog
			if cat:
				await _enviar_catalogo(update, "Catálogo:", (cat, modo))
			await msg.reply_text("Cuando quieras, envía tu dirección o comparte la ubicación en el mapa.")
			return PIDIENDO_UBICACION
		ubicacion = t_raw

	if not ubicacion:
		await msg.reply_text(
			generar_respuesta_natural(
				"El cliente no envio una ubicacion valida para delivery.",
				contexto="en un chatbot de helados se debe pedir una ubicacion valida de forma natural",
				objetivo="pide una ubicacion o direccion valida para delivery en una sola frase",
			)
		)
		return PIDIENDO_UBICACION

	context.user_data["ubicacion_entrega"] = ubicacion
	context.user_data["telegram_id"] = update.effective_user.id
	context.user_data["delivery_admin_notified"] = False
	preparar_delivery_pendiente(update.effective_user.id)
	await msg.reply_text("Ubicación recibida. Estamos cotizando el envío; le informaremos el costo en breve.")
	await _notify_admin_delivery_location(update, context, ubicacion)
	return ESPERANDO_COSTO_DELIVERY


async def esperar_costo_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Bloquea el avance hasta que admin asigne costo de delivery."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	telegram_id = update.effective_user.id
	texto = (update.message.text or "").strip()

	# Mismo chat como admin y cliente: un número solo (ej. 5 o 50) es el monto USD de delivery,
	# no un producto.
	if (
		texto
		and ADMIN_TELEGRAM_ID is not None
		and telegram_id == ADMIN_TELEGRAM_ID
		and obtener_delivery_pendiente() == telegram_id
		and obtener_costo_delivery_pendiente(telegram_id) is None
	):
		norm = texto.replace(",", ".").strip()
		if norm and re.fullmatch(r"\d+(?:\.\d+)?", norm):
			try:
				monto = float(norm)
			except ValueError:
				monto = None
			if monto is not None and monto >= 0:
				guardar_costo_delivery_pendiente(telegram_id, monto)
				await update.message.reply_text(f"Costo de envío asignado: ${monto:.2f} USD.")
				await context.bot.send_message(
					chat_id=telegram_id,
					text=(f"Costo de envío registrado: ${monto:.2f} USD.\n{_TEXTO_CLIENTE_TRAS_ASIGNAR_ENVIO}"),
					reply_markup=_markup_metodo_pago(),
				)
				return PIDIENDO_METODO_PAGO

	costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)

	if costo_pendiente is None:
		await update.message.reply_text(
			"Aún estamos confirmando el costo de envío. En cuanto esté listo, podrá elegir el método de pago."
		)
		return ESPERANDO_COSTO_DELIVERY

	metodo_guardado = context.user_data.get("metodo_pago")
	if metodo_guardado in {"efectivo", "pago movil"}:
		await update.message.reply_text(await _build_resumen_pedido_async(context))
		return CONFIRMANDO_PEDIDO

	_, metodo_pago = _extract_delivery_and_payment(texto)
	if metodo_pago in {"efectivo", "pago movil"}:
		return await recibir_metodo_pago(update, context)

	await update.message.reply_text(
		f"Ya tenemos el costo de envío. {_TEXTO_CLIENTE_TRAS_ASIGNAR_ENVIO}",
		reply_markup=_markup_metodo_pago(),
	)
	return PIDIENDO_METODO_PAGO


async def recibir_metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Guarda método de pago para delivery y solicita comprobante."""

	out = await _cancelar_si_en_texto(update, context)
	if out is not None:
		return out
	texto = (update.message.text or "").strip()
	_, metodo_pago = _extract_delivery_and_payment(texto)
	if metodo_pago not in {"efectivo", "pago movil"}:
		await update.message.reply_text(
			"Escriba «efectivo» o «pago móvil» (sin tilde también vale), o use los botones.",
			reply_markup=_markup_metodo_pago(),
		)
		return PIDIENDO_METODO_PAGO

	if metodo_pago == "pago movil":
		# Para pago móvil necesitamos conversión Bs (BCV). Si no hay tasa disponible, no avanzamos.
		try:
			obtener_tasa_usd_bcv(timeout_s=4.0)
		except Exception:
			await update.message.reply_text(
				"No pude obtener la tasa BCV para calcular el monto en bolívares. "
				"Intenta de nuevo en unos minutos, o elige **efectivo** si puedes pagar en USD en persona."
			)
			return PIDIENDO_METODO_PAGO

	context.user_data["metodo_pago"] = metodo_pago
	await update.message.reply_text(await _build_resumen_pedido_async(context))
	return CONFIRMANDO_PEDIDO


async def recibir_comprobante(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Recibe la foto del comprobante y guarda el pedido delivery."""

	if not update.message.photo:
		await update.message.reply_text("Debes enviar una foto del comprobante.")
		return PIDIENDO_COMPROBANTE

	context.user_data["comprobante_file_id"] = update.message.photo[-1].file_id

	telegram_id = update.effective_user.id
	urow = obtener_usuario_por_telegram_id(telegram_id)
	nombre = nombre_publico_usuario(urow) if urow else ""
	if not nombre:
		nombre = update.effective_user.first_name or update.effective_user.username or "Usuario"
	tipo_entrega = context.user_data.get("tipo_entrega")
	metodo_pago = context.user_data.get("metodo_pago")
	ubicacion_entrega = context.user_data.get("ubicacion_entrega")
	comprobante_file_id = context.user_data.get("comprobante_file_id")
	items = context.user_data.get("items")

	delivery_costo_pendiente = 0
	if tipo_entrega == "delivery":
		costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)
		if costo_pendiente is not None:
			delivery_costo_pendiente = float(costo_pendiente)
		else:
			await update.message.reply_text(
				"Aún estamos confirmando el costo de envío. Cuando lo tengamos, podrá enviar el comprobante."
			)
			return PIDIENDO_COMPROBANTE

	items_resueltos = context.user_data.get("items_guardados") or context.user_data.get("items")
	items_simple_cf = _items_desde_context(context)
	if items_simple_cf:
		cumple_cf, sub_cf, umb_cf = validar_minimo_compra_mayor(items_simple_cf, context.user_data.get("modo_precio"))
		if not cumple_cf:
			await update.message.reply_text(
				f"En modo al mayor el pedido debe sumar al menos ${float(umb_cf):.2f} USD a precio mayorista. "
				f"Llevas ${float(sub_cf):.2f}. Cancela este pedido y haz uno nuevo con más productos."
			)
			return CONFIRMANDO_PEDIDO
	try:
		if items_resueltos:
			pedido_id = crear_pedido(
				telegram_id=telegram_id,
				nombre=nombre,
				producto=context.user_data.get("producto", items_resueltos[0]["producto"]),
				cantidad=int(context.user_data.get("cantidad", items_resueltos[0]["cantidad"])),
				tipo_entrega=tipo_entrega,
				ubicacion_entrega=ubicacion_entrega,
				delivery_costo_usd=delivery_costo_pendiente,
				metodo_pago=metodo_pago,
				comprobante_file_id=comprobante_file_id,
				items=items_resueltos,
				modo_precio=context.user_data.get("modo_precio"),
			)
		else:
			producto = context.user_data["producto"]
			cantidad = context.user_data["cantidad"]
			pedido_id = crear_pedido(
				telegram_id=telegram_id,
				nombre=nombre,
				producto=producto,
				cantidad=cantidad,
				tipo_entrega=tipo_entrega,
				ubicacion_entrega=ubicacion_entrega,
				delivery_costo_usd=delivery_costo_pendiente,
				metodo_pago=metodo_pago,
				comprobante_file_id=comprobante_file_id,
				modo_precio=context.user_data.get("modo_precio"),
			)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		context.user_data.clear()
		return ConversationHandler.END

	await update.message.reply_text(
		f"Pedido #{pedido_id} registrado. Recibimos su comprobante; lo verificaremos y le confirmaremos el estado."
	)

	if ADMIN_TELEGRAM_ID:
		_det, _pie = _texto_admin_entrega_delivery(tipo_entrega, ubicacion_entrega, delivery_costo_pendiente)
		texto_admin = (
			f"Nuevo pedido #{pedido_id}\n"
			f"Usuario: {nombre} ({telegram_id})\n"
			f"{_usuario_admin_ci_tel(urow)}\n"
			f"Entrega: {tipo_entrega}\n"
			f"{_det}"
			f"Pago: {metodo_pago}\n"
			f"{_pie}"
		)
		await context.bot.send_message(
			chat_id=ADMIN_TELEGRAM_ID,
			text=texto_admin,
			reply_markup=_build_admin_actions_markup(tipo_entrega, pedido_id),
		)
		await context.bot.send_photo(
			chat_id=ADMIN_TELEGRAM_ID,
			photo=comprobante_file_id,
			caption=f"Comprobante del pedido #{pedido_id}",
		)

	context.user_data.clear()
	return ConversationHandler.END


async def confirmar_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Guarda el pedido si el usuario confirma."""

	texto_raw = update.message.text or ""

	if _texto_es_cancelacion(texto_raw):
		return await cancelar_pedido(update, context)

	if _texto_negativo(texto_raw):
		await update.message.reply_text(
			"De acuerdo, **no** registré el pedido. Cuando quieras puedes usar /pedido o escribir lo que necesitas."
		)
		context.user_data.clear()
		return ConversationHandler.END

	if _texto_afirmativo(texto_raw):
		tipo_entrega = context.user_data["tipo_entrega"]
		if tipo_entrega == "delivery" and not context.user_data.get("comprobante_file_id"):
			context.user_data["esperando_comprobante"] = True
			await update.message.reply_text(
				"Perfecto. Ahora envía la foto del comprobante para completar tu pedido.\n\n"
				"Si necesitas cambiar algo después de esta confirmación, cancela el pedido y haz uno nuevo."
			)
			return PIDIENDO_COMPROBANTE

		telegram_id = update.effective_user.id
		urow = obtener_usuario_por_telegram_id(telegram_id)
		nombre = nombre_publico_usuario(urow) if urow else ""
		if not nombre:
			nombre = update.effective_user.first_name or update.effective_user.username or "Usuario"
		metodo_pago = context.user_data.get("metodo_pago")
		ubicacion_entrega = context.user_data.get("ubicacion_entrega")
		comprobante_file_id = context.user_data.get("comprobante_file_id")
		items = context.user_data.get("items_guardados") or context.user_data.get("items")
		items_simple_cf = _items_desde_context(context)
		if items_simple_cf:
			cumple_cf, sub_cf, umb_cf = validar_minimo_compra_mayor(items_simple_cf, context.user_data.get("modo_precio"))
			if not cumple_cf:
				await update.message.reply_text(
					f"En modo al mayor el pedido debe sumar al menos ${float(umb_cf):.2f} USD a precio mayorista. "
					f"Llevas ${float(sub_cf):.2f}. Cancela este pedido y haz uno nuevo con más productos."
				)
				return CONFIRMANDO_PEDIDO
		# compatibilidad: si no hay items en DB shape, usar producto/cantidad anteriores
		if not items:
			producto = context.user_data["producto"]
			cantidad = context.user_data["cantidad"]
		delivery_costo_pendiente = 0
		if tipo_entrega == "delivery":
			costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)
			if costo_pendiente is not None:
				delivery_costo_pendiente = float(costo_pendiente)
			else:
				await update.message.reply_text(
					"Aún estamos confirmando el costo de envío. Espere un momento e inténtelo de nuevo."
				)
				return CONFIRMANDO_PEDIDO
		pedido_id = None
		try:
			if items:
				pedido_id = crear_pedido(
					telegram_id=telegram_id,
					nombre=nombre,
					producto=context.user_data.get("producto", items[0]["producto"]),
					cantidad=int(context.user_data.get("cantidad", items[0]["cantidad"])),
					tipo_entrega=tipo_entrega,
					ubicacion_entrega=ubicacion_entrega,
					delivery_costo_usd=delivery_costo_pendiente,
					metodo_pago=metodo_pago,
					comprobante_file_id=comprobante_file_id,
					items=items,
					modo_precio=context.user_data.get("modo_precio"),
				)
			else:
				pedido_id = crear_pedido(
					telegram_id=telegram_id,
					nombre=nombre,
					producto=producto,
					cantidad=cantidad,
					tipo_entrega=tipo_entrega,
					ubicacion_entrega=ubicacion_entrega,
					delivery_costo_usd=delivery_costo_pendiente,
					metodo_pago=metodo_pago,
					comprobante_file_id=comprobante_file_id,
					modo_precio=context.user_data.get("modo_precio"),
				)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			context.user_data.clear()
			return ConversationHandler.END

		if tipo_entrega == "delivery":
			await update.message.reply_text(
				f"Pedido #{pedido_id} registrado. Recibimos su comprobante; lo verificaremos y le confirmaremos el estado."
			)
		else:
			await update.message.reply_text(
				f"Pedido #{pedido_id} registrado para retiro en tienda. "
				"Le avisaremos al concluir el proceso de pago presencial."
			)

		if ADMIN_TELEGRAM_ID:
			_det, _pie = _texto_admin_entrega_delivery(tipo_entrega, ubicacion_entrega, delivery_costo_pendiente)
			texto_admin = (
				f"Nuevo pedido #{pedido_id}\n"
				f"Usuario: {nombre} ({telegram_id})\n"
				f"{_usuario_admin_ci_tel(urow)}\n"
				f"Entrega: {tipo_entrega}\n"
				f"{_det}"
				f"Pago: {metodo_pago}\n"
				f"{_pie}"
			)
			await context.bot.send_message(
				chat_id=ADMIN_TELEGRAM_ID,
				text=texto_admin,
				reply_markup=_build_admin_actions_markup(tipo_entrega, pedido_id),
			)

			if tipo_entrega == "delivery" and comprobante_file_id:
				await context.bot.send_photo(
					chat_id=ADMIN_TELEGRAM_ID,
					photo=comprobante_file_id,
					caption=f"Comprobante del pedido #{pedido_id}",
				)

		context.user_data.clear()
		return ConversationHandler.END

	await update.message.reply_text(
		"No entendí.\n\n" + _texto_edicion_bloqueada_tras_confirmar()
	)
	return CONFIRMANDO_PEDIDO


async def admin_recibir_monto_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Permite al admin enviar un monto directo tras revisar la ubicación de un delivery."""

	if not _check_admin(update):
		return

	telegram_id = obtener_delivery_pendiente()
	if telegram_id is None:
		# Ignora texto libre del admin cuando no hay delivery pendiente por cotizar.
		return

	texto = (update.message.text or "").strip().replace(",", ".")
	try:
		monto = float(texto)
	except ValueError:
		await update.message.reply_text("Monto inválido. Envía solo un número, por ejemplo: x")
		return

	if monto < 0:
		return

	guardar_costo_delivery_pendiente(telegram_id, monto)
	await update.message.reply_text(f"Costo de envío asignado: ${monto:.2f} USD.")
	await context.bot.send_message(
		chat_id=telegram_id,
		text=(f"Costo de envío registrado: ${monto:.2f} USD.\n{_TEXTO_CLIENTE_TRAS_ASIGNAR_ENVIO}"),
		reply_markup=_markup_metodo_pago(),
	)


async def cancelar_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Cancela el flujo de conversación."""

	tid = update.effective_user.id
	urow = obtener_usuario_por_telegram_id(tid)
	nombre = nombre_publico_usuario(urow) if urow else ""
	context.user_data.clear()
	msg = update.effective_message
	if msg:
		if nombre:
			await msg.reply_text(f"Hasta pronto, {nombre}. Proceso cancelado.")
		else:
			await msg.reply_text("Proceso cancelado.")
	return ConversationHandler.END


async def _cancelar_si_en_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
	"""Si el mensaje pide cancelar el flujo, cancela y devuelve END; si no, None."""

	msg = update.effective_message
	if msg and msg.text and _texto_es_cancelacion(msg.text):
		return await cancelar_pedido(update, context)
	return None


def _texto_es_cancelacion(texto: str) -> bool:
	t = (texto or "").strip().lower()
	if not t:
		return False
	if t.startswith("/cancelar"):
		return True
	if t in {
		"cancelar",
		"cancela",
		"cancelalo",
		"cancelar orden",
		"cancelar pedido",
		"quiero cancelar",
		"quiero cancelar el pedido",
		"no quiero el pedido",
		"salir",
		"salir del pedido",
		"abortar",
		"anular",
		"anula",
		"anulalo",
		"anular orden",
		"anular pedido",
		"suspender pedido",
		"descartar pedido",
	}:
		return True
	# Frases cortas que piden cancelar sin confundir con "no quiero al mayor" u otras negaciones largas.
	if any(p in t for p in ("cancelar", "anular", "anula", "descartar", "suspender")) and any(
		p in t for p in ("pedido", "orden", "compra")
	) and len(t) < 100:
		return True
	if t.startswith("quiero cancelar") and len(t) < 80:
		return True
	return False


def _texto_recordatorio_nombre_catalogo() -> str:
	"""Texto breve para pedir nombre exacto y explicar límites del coloquial regional."""

	return (
		"Escribe cada producto tal como aparece en el **catálogo** (o envía la palabra **catálogo** para ver la lista). "
		"Si usas nombres muy coloquiales (por ejemplo «barquilla» por cono, «potecitos» por tinas o «pote» por helado en litros), "
		"a veces no los enlazo con una línea exacta hasta que uses el nombre del listado."
	)


def _mensaje_terminos_no_catalogo(terminos: list[str]) -> str:
	"""Marca lo que no encajó y pide el nombre del catálogo."""

	if not terminos:
		return ""
	return (
		"No reconocí bien en el catálogo: **"
		+ "**, **".join(terminos)
		+ "**.\n"
		+ _texto_recordatorio_nombre_catalogo()
	)


async def _recalcular_items_guardados_si_hay_carrito(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	items_simple = _items_desde_context(context)
	if not items_simple:
		return
	try:
		context.user_data["items_guardados"] = _preparar_items_guardados(
			items_simple, context.user_data.get("modo_precio")
		)
	except ValueError as exc:
		await update.message.reply_text(str(exc))


def _subtotal_carrito_actual_usd(context: ContextTypes.DEFAULT_TYPE) -> float:
	"""Calcula subtotal del carrito (sin delivery) con el modo de precio actual."""

	items_simple = _items_desde_context(context)
	if not items_simple:
		return 0.0
	modo = context.user_data.get("modo_precio")
	total = 0.0
	for item in items_simple:
		producto = str(item.get("producto", "")).strip()
		cantidad = int(item.get("cantidad", 0) or 0)
		if not producto or cantidad <= 0:
			continue
		montos = obtener_resumen_montos(
			producto,
			cantidad,
			delivery_costo_usd=0,
			modo_precio=modo,
			incluir_bcv=False,
		)
		total += float(montos.get("subtotal_usd", 0.0) or 0.0)
	return total


async def _remover_item_del_carrito_por_fragmento(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	fragmento: str,
	catalog: list[dict],
) -> int:
	"""Quita del carrito lo que el usuario niega explícitamente (ya no quiero / quita / borra)."""

	qty_remove, prod_raw, qty_exp = _parse_qty_name_detail(fragmento)
	producto, sugs = _resolver_producto_en_carrito(prod_raw, context, catalog)
	if not producto:
		await update.message.reply_text(
			"No ubico cuál producto de tu pedido quieres quitar.\n"
			+ ("\n".join(f"- {x}" for x in sugs[:5]) if sugs else _texto_ayuda_editar_pedido(context))
		)
		return PIDIENDO_PRODUCTO

	items_list = list(context.user_data.get("items") or [])
	idx = next((i for i, it in enumerate(items_list) if str(it.get("producto")) == producto), None)
	if idx is not None:
		q = int(items_list[idx].get("cantidad", 0) or 0)
		before = items_list[:idx]
		after = items_list[idx + 1 :]
		if qty_exp:
			remove_n = max(1, min(int(qty_remove), q))
			nueva = q - remove_n
			if nueva > 0:
				new_items = before + [{"producto": producto, "cantidad": nueva}] + after
			else:
				new_items = before + after
		else:
			new_items = before + after
		context.user_data["items"] = new_items
	elif context.user_data.get("producto") == producto:
		context.user_data.pop("producto", None)
		context.user_data.pop("cantidad", None)
		context.user_data.pop("stock_disponible", None)
		new_items = items_list
	else:
		await update.message.reply_text(f"No tenías **{producto}** en el pedido.")
		return PIDIENDO_PRODUCTO

	new_items = context.user_data.get("items") or []
	if new_items:
		try:
			context.user_data["items_guardados"] = _preparar_items_guardados(
				new_items, context.user_data.get("modo_precio")
			)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return PIDIENDO_PRODUCTO
	else:
		context.user_data.pop("items_guardados", None)

	tail = (
		"\n\n" + _texto_pregunta_mas_productos(context)
		if new_items
		else "\n\nTu carrito quedó vacío; dime qué productos quieres."
	)
	await update.message.reply_text(f"Entendido, he quitado **{producto}** de tu pedido.{tail}")
	return PIDIENDO_PRODUCTO


async def _procesar_cambios_pre_confirmacion(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
	tid: int,
) -> int | None:
	"""Permite cambiar detal/mayor, delivery/pickup o método de pago antes de registrar el pedido."""

	tn = normalize_text(texto)
	modo = context.user_data.get("modo_precio")
	if not modo:
		return None
	if _texto_tiene_items_o_producto_pedido(texto, catalog):
		return None
	loc_directa = _extract_location_from_text(texto)

	if re.search(
		r"\b("
		r"(?:cambi(?:ar|o|a))\s+(?:a|al)\s+(?:precio\s+)?(?:al\s+)?(?:mayor|mayorista)|"
		r"quiero\s+(?:el\s+)?precio\s+(?:de\s+)?(?:mayorista|mayor)|"
		r"(?:pasar(?:me)?|pasame|pásame)\s+(?:a|al)\s+(?:mayor|mayorista)|"
		r"(?:al\s+)?mayor|precio\s+mayor|mayorista"
		r")\b",
		tn,
	) or _frase_clave_en_texto(tn, _MAYOR_KEYWORDS):
		if modo != "mayor":
			context.user_data["modo_precio"] = "mayor"
			await _recalcular_items_guardados_si_hay_carrito(update, context)
			subtotal = _subtotal_carrito_actual_usd(context)
			await update.message.reply_text(
				"Listo: pasamos a **precio al mayor**. "
				f"Subtotal actualizado del carrito: **${subtotal:.2f} USD**."
			)
			return _infer_estado_tras_volver_al_chat(context, tid)

	if re.search(
		r"\b("
		r"(?:cambi(?:ar|o|a))\s+(?:a|al)\s+(?:precio\s+)?(?:al\s+)?(?:detal|menudeo)|"
		r"(?:pasar(?:me)?|pasame|pásame)\s+(?:a|al)\s+(?:detal|menudeo)|"
		r"detal|al\s+detal|menudeo"
		r")\b",
		tn,
	) or ("detalle" in tn and "mayor" not in tn) or (
		_frase_clave_en_texto(tn, _DETAL_KEYWORDS) and not _frase_clave_en_texto(tn, _MAYOR_KEYWORDS)
	):
		if modo != "detal":
			context.user_data["modo_precio"] = "detal"
			await _recalcular_items_guardados_si_hay_carrito(update, context)
			subtotal = _subtotal_carrito_actual_usd(context)
			await update.message.reply_text(
				"Listo: pasamos a **precio al detal**. "
				f"Subtotal actualizado del carrito: **${subtotal:.2f} USD**."
			)
			return _infer_estado_tras_volver_al_chat(context, tid)

	texto_items = strip_control_commands_for_product_search(texto)
	if texto_items:
		parsed = _parse_order_message(texto_items, catalog, _infer_estado_tras_volver_al_chat(context, tid))
		estado_rem: int | None = None
		if parsed.remove_product_query:
			estado_rem = await _remover_item_del_carrito_por_fragmento(
				update, context, parsed.remove_product_query, catalog
			)
		pending_items = parsed.ambiguos
		if parsed.items:
			try:
				await _sumar_items_al_carrito(update, context, parsed.items)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return PIDIENDO_PRODUCTO
			_aplicar_parse_slots(context, parsed)
			context.user_data.pop("esperando_comprobante", None)
			context.user_data.pop("comprobante_file_id", None)
			if pending_items:
				context.user_data["items_pendientes_clarificar"] = pending_items
				await update.message.reply_text(
					_formatear_item_ambiguo(pending_items[0], 1, len(pending_items))
				)
				return PIDIENDO_PRODUCTO
			if estado_rem is not None:
				await update.message.reply_text(
					"También dejé registrados los productos nuevos en tu carrito y mantuve los datos del pedido."
				)
			else:
				await update.message.reply_text("Listo, agregué ese producto al carrito y mantuve los datos del pedido.")
			return await _continuar_pedido_tras_carrito(update, context)

		if pending_items:
			context.user_data["items_pendientes_clarificar"] = pending_items
			await update.message.reply_text(_formatear_item_ambiguo(pending_items[0], 1, len(pending_items)))
			return PIDIENDO_PRODUCTO

		if estado_rem is not None:
			return estado_rem

	te_actual = context.user_data.get("tipo_entrega")
	if te_actual == "delivery" and context.user_data.get("ubicacion_entrega") and (
		_texto_editar_ubicacion(texto)
		or (
			loc_directa
			and any(
				palabra in tn
				for palabra in ("cambiar", "cambio", "editar", "corregir", "otra", "nueva", "direccion", "ubicacion")
			)
		)
	):
		_reset_cotizacion_delivery(context, tid)
		context.user_data.pop("comprobante_file_id", None)
		context.user_data.pop("esperando_comprobante", None)
		if loc_directa:
			context.user_data["ubicacion_entrega"] = loc_directa
			preparar_delivery_pendiente(tid)
			await _notify_admin_delivery_location(update, context, loc_directa)
			await update.message.reply_text(
				"Dirección actualizada. Anulamos la cotización anterior; le confirmaremos el nuevo costo de envío en breve."
			)
			return ESPERANDO_COSTO_DELIVERY
		await update.message.reply_text(
			"Listo: borré la dirección anterior y la cotización vieja. Envíame la nueva ubicación o escríbela."
		)
		return PIDIENDO_UBICACION

	if te_actual in ("delivery", "pickup") and re.search(r"\b(pickup|pick\s*up|retiro|recojo|recoger|por tienda|en tienda)\b", tn):
		if te_actual != "pickup":
			_reset_cotizacion_delivery(context, tid)
			context.user_data["tipo_entrega"] = "pickup"
			context.user_data["metodo_pago"] = "presencial"
			context.user_data["ubicacion_entrega"] = None
			context.user_data["comprobante_file_id"] = None
			context.user_data.pop("esperando_comprobante", None)
			await update.message.reply_text("Cambié a **pickup** (retiro en tienda).")
			return await _continuar_pedido_tras_carrito(update, context)

	if te_actual in ("delivery", "pickup") and re.search(r"\b(delivery|domicilio|envio|a domicilio)\b", tn):
		if te_actual != "delivery":
			context.user_data["tipo_entrega"] = "delivery"
			context.user_data["metodo_pago"] = None
			context.user_data["ubicacion_entrega"] = None
			context.user_data.pop("comprobante_file_id", None)
			context.user_data.pop("esperando_comprobante", None)
			if loc_directa:
				context.user_data["ubicacion_entrega"] = loc_directa
				preparar_delivery_pendiente(tid)
				await _notify_admin_delivery_location(update, context, loc_directa)
				await update.message.reply_text(
					"Modo envío a domicilio con la nueva dirección. Le confirmaremos el costo de envío en breve."
				)
				return ESPERANDO_COSTO_DELIVERY
			preparar_delivery_pendiente(tid)
			await update.message.reply_text("Modo envío a domicilio. Envíe su ubicación o escriba la dirección completa.")
			return PIDIENDO_UBICACION

	mp = context.user_data.get("metodo_pago")
	if context.user_data.get("tipo_entrega") == "delivery":
		if re.search(r"\b(efectivo|cash)\b", tn) and mp != "efectivo":
			context.user_data["metodo_pago"] = "efectivo"
			context.user_data.pop("comprobante_file_id", None)
			context.user_data.pop("esperando_comprobante", None)
			await update.message.reply_text("Entendido: pagarás en **efectivo** al recibir.")
			return await _continuar_pedido_tras_carrito(update, context)
		if re.search(r"\b(pago movil|pago móvil|pago movil|transferencia|transfer)\b", tn) and mp != "pago movil":
			context.user_data["metodo_pago"] = "pago movil"
			context.user_data.pop("comprobante_file_id", None)
			await update.message.reply_text("Entendido: **pago móvil**.")
			return await _continuar_pedido_tras_carrito(update, context)

	return None


def _parse_pedido_id(context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Obtiene el ID del pedido desde argumentos del comando admin."""

	if not context.args:
		raise ValueError("Debes indicar el ID del pedido. Ejemplo: /admin_confirmar <numero_de_pedido>")
	try:
		return int(context.args[0])
	except ValueError as exc:
		raise ValueError("El ID del pedido debe ser numérico.") from exc


def _check_admin(update: Update) -> bool:
	"""Valida si el usuario actual es el administrador configurado."""

	if ADMIN_TELEGRAM_ID is None:
		return False
	return update.effective_user.id == ADMIN_TELEGRAM_ID


def _admin_menu_text() -> str:
	"""Devuelve el menú principal del modo admin."""

	return (
		"Modo admin activado.\n\n"
		"Puedes escribir de forma natural, por ejemplo:\n"
		"- pedidos pendientes\n"
		"- pedido más reciente\n"
		"- ver chat pedido <numero_de_pedido>\n"
		"- confirmar pedido <numero_de_pedido>\n"
		"- marcar entregado pedido <numero_de_pedido>\n"
		"- concluir pedido <numero_de_pedido>\n"
		"- asignar delivery pedido <numero_de_pedido> <monto>\n"
		"- ver productos\n"
		"- subir stock helados <cantidad>\n"
		"- cambiar precio helados <monto>\n"
		"- ver pago movil\n\n"
		"Para salir del modo admin escribe /salir_admin o 'salir admin'."
	)


def _pedido_mas_reciente() -> dict | None:
	"""Devuelve el pedido con ID más alto como último pedido registrado."""

	pedidos = obtener_pedidos_admin()
	if not pedidos:
		return None
	return max(pedidos, key=lambda pedido: pedido["id"])


def _linea_cliente_pedido(pedido: dict) -> str:
	"""Nombre, cédula, teléfono y Telegram para resúmenes de pedido."""

	nombre = (pedido.get("usuario_nombre") or "").strip() or "—"
	ci = (pedido.get("usuario_cedula") or "").strip() or "—"
	tel = (pedido.get("usuario_telefono") or "").strip() or "—"
	tg = pedido.get("telegram_id")
	tg_str = str(tg) if tg is not None else "—"
	return f"Cliente: {nombre}\nCédula: {ci} | Tel: {tel} | Telegram ID: {tg_str}"


def _cliente_pedido_lista_compacta(pedido: dict) -> str:
	"""Una sola línea con cliente, CI, teléfono y Telegram para listados densos."""

	nombre = (pedido.get("usuario_nombre") or "").strip() or "—"
	ci = (pedido.get("usuario_cedula") or "").strip() or "—"
	tel = (pedido.get("usuario_telefono") or "").strip() or "—"
	tg = pedido.get("telegram_id")
	tg_str = str(tg) if tg is not None else "—"
	return f"{nombre} | CI:{ci} | Tel:{tel} | TG:{tg_str}"


def _usuario_admin_ci_tel(urow: dict | None) -> str:
	"""Línea auxiliar para avisos de nuevo pedido cuando aún no hay dict de pedido."""

	ci = (urow.get("cedula") or "").strip() if urow else ""
	tel = (urow.get("telefono") or "").strip() if urow else ""
	ci = ci or "—"
	tel = tel or "—"
	return f"Cédula: {ci} | Tel: {tel}"


def _formatear_resumen_pedido(pedido: dict) -> str:
	"""Construye un resumen corto y seguro de un pedido."""

	delivery_costo = Decimal(str(pedido.get("delivery_costo_usd", 0))).quantize(Decimal("0.01"))
	revision_delivery = "sí" if pedido.get("delivery_revisado") == 1 else "no"
	items = pedido.get("items") or []
	if items:
		lineas_items = "\n".join(
			f"- {item.get('nombre_producto', 'producto')} x{item.get('cantidad', 1)}"
			for item in items
		)
	else:
		lineas_items = f"- {pedido['nombre_producto']} x{pedido['cantidad']}"
	return (
		f"Pedido #{pedido['id']}\n"
		f"{_linea_cliente_pedido(pedido)}\n"
		f"Productos:\n{lineas_items}\n"
		f"Entrega: {pedido['tipo_entrega']}\n"
		f"Delivery USD: ${delivery_costo}\n"
		f"Delivery revisado: {revision_delivery}\n"
		f"Estado: {pedido['estado']}"
	)


def _first_int(texto: str) -> int | None:
	"""Extrae el primer entero encontrado en un texto."""

	match = re.search(r"\b(\d+)\b", texto)
	if match is None:
		return None
	try:
		return int(match.group(1))
	except ValueError:
		return None


def _first_float(texto: str) -> float | None:
	"""Extrae el primer número decimal encontrado en un texto."""

	match = re.search(r"\b(\d+(?:[\.,]\d+)?)\b", texto)
	if match is None:
		return None
	try:
		return float(match.group(1).replace(",", "."))
	except ValueError:
		return None


async def admin_modo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Activa el modo admin solo para el ID configurado."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	context.user_data["admin_mode"] = True
	await update.message.reply_text(_admin_menu_text())


async def admin_salir_modo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Desactiva el modo admin."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	context.user_data["admin_mode"] = False
	await update.message.reply_text("Saliste del modo admin. Ahora tus mensajes se tratarán como cliente.")


async def _procesar_texto_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> bool:
	"""Intenta ejecutar una acción administrativa a partir de texto natural."""

	t = texto.strip().lower()
	if not t:
		return False

	if t in {"salir admin", "salir del admin", "cerrar admin", "salir modo admin"}:
		context.user_data["admin_mode"] = False
		await update.message.reply_text("Saliste del modo admin. Tus mensajes volverán a tratarse como cliente.")
		return True

	if any(palabra in t for palabra in ("ayuda", "menu", "funciones", "opciones")):
		await update.message.reply_text(_admin_menu_text())
		return True

	pending_user = obtener_delivery_pendiente()
	if pending_user is not None:
		monto_directo = _first_float(t)
		if monto_directo is not None and re.fullmatch(r"\d+(?:[\.,]\d+)?", t):
			guardar_costo_delivery_pendiente(pending_user, monto_directo)
			await update.message.reply_text(f"Costo de envío asignado: ${monto_directo:.2f} USD.")
			await context.bot.send_message(
				chat_id=pending_user,
				text=(
					f"Costo de envío registrado: ${monto_directo:.2f} USD.\n"
					"Ahora responda con «efectivo» o «pago móvil» para continuar su pedido."
				),
			)
			return True

	if any(palabra in t for palabra in ("pedidos pendientes", "pendientes", "listar pedidos", "ver pedidos", "ver pedidos pendientes")):
		pedidos = obtener_pedidos_admin()
		if not pedidos:
			await update.message.reply_text("No hay pedidos registrados.")
			return True

		lineas = []
		for pedido in pedidos[:20]:
			delivery_costo = Decimal(str(pedido.get("delivery_costo_usd", 0))).quantize(Decimal("0.01"))
			revision_delivery = "sí" if pedido.get("delivery_revisado") == 1 else "no"
			lineas.append(
				f"#{pedido['id']} | {_cliente_pedido_lista_compacta(pedido)} | {pedido['nombre_producto']} x{pedido['cantidad']} | {pedido['tipo_entrega']} | delivery ${delivery_costo} | revisado: {revision_delivery} | estado: {pedido['estado']}"
			)
		await update.message.reply_text("Pedidos:\n" + "\n".join(lineas))
		return True

	if any(palabra in t for palabra in ("pedido más reciente", "pedido mas reciente", "último pedido", "ultimo pedido", "pedido reciente")):
		pedido = _pedido_mas_reciente()
		if pedido is None:
			await update.message.reply_text("No hay pedidos registrados.")
			return True
		delivery_costo = Decimal(str(pedido.get("delivery_costo_usd", 0))).quantize(Decimal("0.01"))
		revision_delivery = "sí" if pedido.get("delivery_revisado") == 1 else "no"
		await update.message.reply_text(
			f"Pedido más reciente #{pedido['id']}\n"
			f"{_linea_cliente_pedido(pedido)}\n"
			f"Producto: {pedido['nombre_producto']} x{pedido['cantidad']}\n"
			f"Entrega: {pedido['tipo_entrega']}\n"
			f"Delivery USD: ${delivery_costo}\n"
			f"Delivery revisado: {revision_delivery}\n"
			f"Estado: {pedido['estado']}"
		)
		return True

	if any(palabra in t for palabra in ("ver productos", "productos", "catalogo", "catálogo")):
		productos = obtener_catalogo_admin()
		if not productos:
			await update.message.reply_text("No hay productos cargados.")
			return True
		lineas = []
		for p in productos[:50]:
			et = etiquetas_resumen_linea(p.get("etiquetas") or [])
			lineas.append(
				f"- {p['nombre_producto']} | ${p.get('precio_detal', 0):.2f} "
				f"(mayor ${p.get('precio_mayor', p.get('precio_detal', 0)):.2f}) | stock: {p['cantidad']}{et}"
			)
		await update.message.reply_text("Catálogo:\n" + "\n".join(lineas))
		return True

	if any(palabra in t for palabra in ("ver pago movil", "pago movil", "pago móvil")) and any(
		palabra in t for palabra in ("ver", "mostrar", "datos", "ayuda", "menu")
	):
		datos = obtener_datos_pago_movil()
		await update.message.reply_text(
			"Datos de pago móvil:\n"
			f"- Teléfono: {datos['telefono']}\n"
			f"- Cédula: {datos['cedula']}\n"
			f"- Banco: {datos['banco']}"
		)
		return True

	if any(palabra in t for palabra in ("comprobante", "ver comprobante", "foto comprobante", "mostrar comprobante")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'ver comprobante del pedido <numero_de_pedido>'.")
			return True
		try:
			pedido = obtener_pedido_por_id(pedido_id)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		if pedido is None:
			await update.message.reply_text(f"No existe el pedido #{pedido_id}.")
			return True
		comprobante_file_id = pedido.get("comprobante_file_id")
		if not comprobante_file_id:
			await update.message.reply_text("Este pedido no tiene comprobante adjunto.")
			return True
		await context.bot.send_photo(
			chat_id=update.effective_chat.id,
			photo=comprobante_file_id,
			caption=f"Comprobante del pedido #{pedido_id}",
		)
		return True

	if any(palabra in t for palabra in ("confirmar", "confirmado", "despachar", "enviar", "en camino", "ruta")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'confirmar pedido <numero_de_pedido>'.")
			return True
		try:
			pedido = admin_confirmar_delivery(pedido_id)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		try:
			await asyncio.to_thread(
				enviar_mensaje_telegram,
				int(pedido["telegram_id"]),
				texto_cliente_pedido_en_camino(pedido_id),
			)
		except TelegramNotifyError as exc:
			await update.message.reply_text(f"Pedido #{pedido_id} confirmado, pero no se pudo avisar al cliente: {exc}")
			return True
		await update.message.reply_text(f"Pedido #{pedido_id} confirmado para entrega.")
		return True

	if any(palabra in t for palabra in ("entregado", "entregar", "entregada", "entrega", "recibido", "recibida")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'marcar entregado pedido <numero_de_pedido>'.")
			return True
		try:
			pedido = admin_marcar_entregado_delivery(pedido_id)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		try:
			await asyncio.to_thread(
				enviar_mensaje_telegram,
				int(pedido["telegram_id"]),
				texto_cliente_pedido_entregado(pedido_id),
			)
		except TelegramNotifyError as exc:
			await update.message.reply_text(f"Pedido #{pedido_id} marcado entregado, pero no se pudo avisar al cliente: {exc}")
			return True
		await update.message.reply_text(f"Pedido #{pedido_id} marcado como entregado.")
		return True

	if any(palabra in t for palabra in ("concluir", "concluido", "cerrar", "cerrado", "finalizar", "finalizado", "terminar", "terminado", "pickup")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'concluir pedido <numero_de_pedido>'.")
			return True
		try:
			pedido = admin_concluir_pickup(pedido_id)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		try:
			await asyncio.to_thread(
				enviar_mensaje_telegram,
				int(pedido["telegram_id"]),
				texto_cliente_pedido_concluido_pickup(pedido_id),
			)
		except TelegramNotifyError as exc:
			await update.message.reply_text(f"Pedido #{pedido_id} concluido, pero no se pudo avisar al cliente: {exc}")
			return True
		await update.message.reply_text(f"Pedido #{pedido_id} concluido (pickup).")
		return True

	if re.search(r"\b(?:ver\s+)?pedido\b.*\b\d+\b", t):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'ver pedido <numero_de_pedido>'.")
			return True
		pedido = obtener_pedido_por_id(pedido_id)
		if pedido is None:
			await update.message.reply_text(f"No existe el pedido #{pedido_id}.")
			return True
		await update.message.reply_text(_formatear_resumen_pedido(pedido))
		return True

	if any(palabra in t for palabra in ("chat", "conversacion", "conversación")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'ver chat pedido <numero_de_pedido>'.")
			return True
		try:
			pedido, chat = obtener_chat_admin(pedido_id, limite=30)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		if not chat:
			await update.message.reply_text(f"Pedido #{pedido_id} sin mensajes de chat registrados.")
			return True
		lineas = [f"[{m['fecha_creacion']}] {m['emisor']}: {m['mensaje']}" for m in chat]
		encabezado = (
			f"Chat del pedido #{pedido_id}\n"
			f"{_linea_cliente_pedido(pedido)}\n"
		)
		await update.message.reply_text(encabezado + "\n" + "\n".join(lineas[-20:]))
		return True

	if any(palabra in t for palabra in ("stock", "precio", "editar")):
		match_stock = re.search(r"(?:stock|editar stock|subir stock|aumentar stock|actualizar stock)\s+(.+?)\s+(\d+)\b", t)
		if match_stock is None:
			match_stock = re.search(
				r"(?:sube|subir|aumenta|actualiza|cambia|modifica)\s+el?\s*stock\s+de\s+(.+?)\s+a\s+(\d+)\b",
				t,
			)
		if match_stock:
			nombre = match_stock.group(1).strip()
			nueva_cantidad = int(match_stock.group(2))
			try:
				admin_actualizar_stock(nombre, nueva_cantidad)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return True
			await update.message.reply_text(f"Stock actualizado: {nombre} -> {nueva_cantidad}")
			return True

		match_precio = re.search(r"(?:precio|editar precio|cambiar precio|actualizar precio)\s+(.+?)\s+(\d+(?:[\.,]\d+)?)\b", t)
		if match_precio is None:
			match_precio = re.search(
				r"(?:sube|subir|aumenta|actualiza|cambia|modifica)\s+el?\s*precio\s+de\s+(.+?)\s+a\s+(\d+(?:[\.,]\d+)?)\b",
				t,
			)
		if match_precio:
			nombre = match_precio.group(1).strip()
			nuevo_precio = float(match_precio.group(2).replace(",", "."))
			try:
				# Compatibilidad: actualizar solo precio detal mediante el parser natural
				admin_actualizar_precios(nombre, nuevo_precio, None)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return True
			await update.message.reply_text(f"Precio detal actualizado: {nombre} -> ${nuevo_precio:.2f}")
			return True

	if any(palabra in t for palabra in ("delivery", "ubicacion", "ubicación", "costo")):
		pedido_id = _first_int(t)
		monto = _first_float(t)
		if pedido_id is None or monto is None:
			pending_user = obtener_delivery_pendiente()
			if pending_user is not None and monto is not None:
				guardar_costo_delivery_pendiente(pending_user, monto)
				await update.message.reply_text(f"Costo de envío asignado: ${monto:.2f} USD.")
				await context.bot.send_message(
					chat_id=pending_user,
					text=(
						f"Costo de envío registrado: ${monto:.2f} USD.\n"
						"Ahora responda con «efectivo» o «pago móvil» para continuar su pedido."
					),
				)
				return True
			return False

		try:
			pedido = admin_asignar_costo_delivery(pedido_id, monto)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		montos = obtener_resumen_montos(
			pedido["nombre_producto"],
			pedido["cantidad"],
			delivery_costo_usd=monto,
		)
		await update.message.reply_text(
			f"Costo delivery actualizado en pedido #{pedido_id}.\n"
			f"- Delivery USD: ${montos['delivery_costo_usd']}\n"
			f"- Total USD: ${montos['total_usd']}"
		)
		await context.bot.send_message(
			chat_id=pedido["telegram_id"],
			text=(
				f"Pedido #{pedido_id}: actualizamos el costo de envío.\n"
				f"- Costo de envío: ${montos['delivery_costo_usd']}\n"
				f"- Total a pagar: ${montos['total_usd']}"
			),
		)
		return True

	if any(palabra in t for palabra in ("responder", "mensaje")):
		pedido_id = _first_int(t)
		if pedido_id is None:
			await update.message.reply_text("Indica el pedido. Ejemplo: 'responder pedido <numero_de_pedido> tu_mensaje'.")
			return True
		mensaje = re.sub(r"(?i).*?(?:responder|mensaje)\s*(?:pedido\s*)?#?\s*\d+\s*", "", texto).strip()
		if not mensaje:
			await update.message.reply_text("Escribe el mensaje que quieres enviar al cliente.")
			return True
		try:
			pedido = registrar_mensaje_admin(pedido_id, mensaje)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return True
		await context.bot.send_message(
			chat_id=pedido["telegram_id"],
			text=f"Mensaje de soporte sobre tu pedido #{pedido_id}: {mensaje}",
		)
		await update.message.reply_text("Mensaje enviado al cliente.")
		return True

	return False


async def admin_listar_pedidos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Lista pedidos para seguimiento administrativo."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	pedidos = obtener_pedidos_admin()
	if not pedidos:
		await update.message.reply_text("No hay pedidos registrados.")
		return

	for pedido in pedidos[:20]:
		delivery_costo = Decimal(str(pedido.get("delivery_costo_usd", 0))).quantize(Decimal("0.01"))
		revision_delivery = "sí" if pedido.get("delivery_revisado") == 1 else "no"
		linea_ubicacion = f"Ubicación: {_ubicacion_legible(pedido.get('ubicacion_entrega'))}"
		if pedido["tipo_entrega"] == "delivery" and pedido.get("delivery_revisado") != 1:
			linea_ubicacion = (
				"Ubicación: pendiente de revisión y monto de delivery"
			)
		texto = (
			f"Pedido #{pedido['id']}\n"
			f"{_linea_cliente_pedido(pedido)}\n"
			f"Producto: {pedido['nombre_producto']} x{pedido['cantidad']}\n"
			f"Entrega: {pedido['tipo_entrega']}\n"
			f"{linea_ubicacion}\n"
			f"Delivery USD: ${delivery_costo}\n"
			f"Delivery revisado: {revision_delivery}\n"
			f"Estado: {pedido['estado']}"
		)
		detalle = obtener_pedido_por_id(pedido["id"])
		if (
			pedido["tipo_entrega"] == "delivery"
			and detalle is not None
			and detalle.get("comprobante_file_id")
		):
			try:
				await context.bot.send_photo(
					chat_id=update.effective_chat.id,
					photo=detalle["comprobante_file_id"],
					caption=texto,
					reply_markup=_build_admin_actions_markup(pedido["tipo_entrega"], pedido["id"]),
				)
			except BadRequest:
				# Algunos comprobantes viejos pueden tener file_id inválido o expirado.
				await update.message.reply_text(
					f"{texto}\n\n(Comprobante no disponible: file_id inválido)",
					reply_markup=_build_admin_actions_markup(pedido["tipo_entrega"], pedido["id"]),
				)
		else:
			await update.message.reply_text(
				texto,
				reply_markup=_build_admin_actions_markup(pedido["tipo_entrega"], pedido["id"]),
			)

async def admin_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Muestra ubicación de delivery y obliga asignar costo en el mismo comando."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	if len(context.args) < 2:
		await update.message.reply_text(
			"Uso: /admin_ubicacion <id_pedido> <costo_usd>. Ejemplo: /admin_ubicacion <numero_de_pedido> <monto>"
		)
		return

	try:
		pedido_id = int(context.args[0])
		costo_usd = float(context.args[1])
		pedido = admin_asignar_costo_delivery(pedido_id, costo_usd)
		montos = obtener_resumen_montos(
			pedido["nombre_producto"],
			pedido["cantidad"],
			delivery_costo_usd=costo_usd,
		)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	ubicacion = pedido.get("ubicacion_entrega")
	geo = _parsear_geo(ubicacion)
	if geo is not None:
		await context.bot.send_location(
			chat_id=update.effective_chat.id,
			latitude=geo[0],
			longitude=geo[1],
		)
	else:
		await update.message.reply_text(
			f"Ubicación de pedido #{pedido_id}: {_ubicacion_legible(ubicacion)}"
		)

	mensaje_admin = (
		f"Costo delivery actualizado en pedido #{pedido_id}.\n"
		f"- Delivery USD: ${montos['delivery_costo_usd']}\n"
		f"- Total USD: ${montos['total_usd']}"
	)
	if montos["total_bs"] is not None and montos["tasa_bcv"] is not None:
		mensaje_admin += (
			f"\n- Tasa BCV: {montos['tasa_bcv']} Bs"
			f"\n- Total Bs: {montos['total_bs']} Bs"
		)

	await update.message.reply_text(mensaje_admin)

	mensaje_cliente = (
		f"Tu pedido #{pedido_id} fue revisado por el admin.\n"
		f"- Costo delivery: ${montos['delivery_costo_usd']}\n"
		f"- Total a pagar: ${montos['total_usd']}"
	)
	if montos["total_bs"] is not None:
		mensaje_cliente += f"\n- Equivalente Bs: {montos['total_bs']} Bs"

	try:
		await asyncio.to_thread(enviar_mensaje_telegram, int(pedido["telegram_id"]), mensaje_cliente)
	except TelegramNotifyError as exc:
		await update.message.reply_text(f"Costo guardado, pero no se pudo avisar al cliente por Telegram: {exc}")


async def admin_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Confirma un pedido delivery para iniciar la entrega."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	try:
		pedido_id = _parse_pedido_id(context)
		pedido = admin_confirmar_delivery(pedido_id)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text(f"Pedido #{pedido_id} confirmado para entrega.")
	try:
		await asyncio.to_thread(
			enviar_mensaje_telegram,
			int(pedido["telegram_id"]),
			texto_cliente_pedido_en_camino(pedido_id),
		)
	except TelegramNotifyError as exc:
		await update.message.reply_text(f"No se pudo avisar al cliente por Telegram: {exc}")


async def admin_entregado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Marca un delivery como entregado y descuenta stock."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	try:
		pedido_id = _parse_pedido_id(context)
		pedido = admin_marcar_entregado_delivery(pedido_id)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text(f"Pedido #{pedido_id} marcado como entregado.")
	try:
		await asyncio.to_thread(
			enviar_mensaje_telegram,
			int(pedido["telegram_id"]),
			texto_cliente_pedido_entregado(pedido_id),
		)
	except TelegramNotifyError as exc:
		await update.message.reply_text(f"No se pudo avisar al cliente por Telegram: {exc}")


async def admin_concluir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Concluye pedido pickup y descuenta stock después de pago presencial."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	try:
		pedido_id = _parse_pedido_id(context)
		pedido = admin_concluir_pickup(pedido_id)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text(f"Pedido #{pedido_id} concluido (pickup).")
	try:
		await asyncio.to_thread(
			enviar_mensaje_telegram,
			int(pedido["telegram_id"]),
			texto_cliente_pedido_concluido_pickup(pedido_id),
		)
	except TelegramNotifyError as exc:
		await update.message.reply_text(f"No se pudo avisar al cliente por Telegram: {exc}")


async def admin_comprobante(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Envía el comprobante de un pedido al chat del admin."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	try:
		pedido_id = _parse_pedido_id(context)
		pedido = obtener_pedido_por_id(pedido_id)
		if pedido is None:
			raise ValueError("El pedido no existe.")
		comprobante_file_id = pedido.get("comprobante_file_id")
		if not comprobante_file_id:
			raise ValueError("Este pedido no tiene comprobante adjunto.")
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await context.bot.send_photo(
		chat_id=update.effective_chat.id,
		photo=comprobante_file_id,
		caption=f"Comprobante del pedido #{pedido_id}",
	)


async def admin_accion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Procesa acciones del admin desde botones inline en Telegram."""

	query = update.callback_query
	if query is None:
		return

	if not _check_admin(update):
		await _answer_callback_query_safe(query)
		try:
			await query.edit_message_text("No autorizado.")
		except BadRequest:
			pass
		return

	data = query.data or ""
	parts = data.split(":")
	if len(parts) != 3 or parts[0] != "admin":
		await _answer_callback_query_safe(query, text="Acción no válida.", show_alert=True)
		return

	action = parts[1]
	try:
		pedido_id = int(parts[2])
	except ValueError:
		await _answer_callback_query_safe(query, text="ID de pedido inválido.", show_alert=True)
		return

	try:
		if action == "confirmar":
			pedido = admin_confirmar_delivery(pedido_id)
			texto_admin = f"Pedido #{pedido_id} confirmado para entrega."
			texto_cliente = texto_cliente_pedido_en_camino(pedido_id)
		elif action == "entregado":
			pedido = admin_marcar_entregado_delivery(pedido_id)
			texto_admin = f"Pedido #{pedido_id} marcado como entregado."
			texto_cliente = texto_cliente_pedido_entregado(pedido_id)
		elif action == "concluir":
			pedido = admin_concluir_pickup(pedido_id)
			texto_admin = f"Pedido #{pedido_id} concluido (pickup)."
			texto_cliente = texto_cliente_pedido_concluido_pickup(pedido_id)
		elif action == "comprobante":
			pedido = obtener_pedido_por_id(pedido_id)
			if pedido is None:
				raise ValueError("El pedido no existe.")
			comprobante_file_id = pedido.get("comprobante_file_id")
			if not comprobante_file_id:
				raise ValueError("Este pedido no tiene comprobante adjunto.")
			await context.bot.send_photo(
				chat_id=update.effective_chat.id,
				photo=comprobante_file_id,
				caption=f"Comprobante del pedido #{pedido_id}",
			)
			await _answer_callback_query_safe(query, text="Comprobante enviado al chat.", show_alert=False)
			return
		else:
			await _answer_callback_query_safe(query, text="Acción no reconocida.", show_alert=True)
			return
	except ValueError as exc:
		await _answer_callback_query_safe(query, text=str(exc), show_alert=True)
		return

	await _answer_callback_query_safe(query)
	try:
		await asyncio.to_thread(enviar_mensaje_telegram, int(pedido["telegram_id"]), texto_cliente)
	except TelegramNotifyError as exc:
		texto_admin = f"{texto_admin}\n\n(Aviso: no se pudo notificar al cliente por Telegram: {exc})"

	resumen = (
		f"Pedido #{pedido_id}\n"
		f"{_linea_cliente_pedido(pedido)}\n"
		f"Producto: {pedido['nombre_producto']} x{pedido['cantidad']}\n"
		f"Entrega: {pedido['tipo_entrega']}\n"
		f"Estado: {pedido['estado']}"
	)
	await query.edit_message_text(text=f"{texto_admin}\n\n{resumen}")


async def cliente_soporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Permite al cliente enviar mensajes al admin asociados a un pedido."""

	if len(context.args) < 2:
		await update.message.reply_text(
			"Uso: /soporte <id_pedido> <mensaje>. Ejemplo: /soporte <numero_de_pedido> <mensaje>"
		)
		return

	try:
		pedido_id = int(context.args[0])
	except ValueError:
		await update.message.reply_text("El id del pedido debe ser numérico.")
		return

	mensaje = " ".join(context.args[1:]).strip()
	if not mensaje:
		await update.message.reply_text("Debes escribir un mensaje para soporte.")
		return
	# Analizar sentimiento del mensaje
	try:
		polaridad = analizar_sentimiento(mensaje)
	except Exception:
		polaridad = 0.0

	if polaridad < -0.05:
		# Respuesta inmediata al usuario informando priorización
		await update.message.reply_text(
			"He detectado que tienes un problema, he marcado tu caso como prioritario para un asesor humano"
		)
	try:
		pedido = registrar_mensaje_cliente(
			pedido_id, update.effective_user.id, mensaje, polaridad=polaridad
		)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text("Tu mensaje fue enviado al admin.")

	if ADMIN_TELEGRAM_ID:
		await context.bot.send_message(
			chat_id=ADMIN_TELEGRAM_ID,
			text=(
				f"Mensaje cliente en pedido #{pedido_id}\n"
				f"{_linea_cliente_pedido(pedido)}\n"
				f"Texto: {mensaje}\n\n"
				f"Polaridad: {polaridad}\n"
				f"Responder: /admin_responder {pedido_id} <mensaje>\n"
				f"Ver chat: /admin_chat {pedido_id}"
			),
		)


def _extract_delivery_and_payment(text: str) -> tuple[str | None, str | None]:
	"""Extrae tipo de entrega y método de pago cuando aparecen en texto natural."""

	t = normalize_text(text)
	tipo_entrega = None
	metodo_pago = None

	# Señales fuertes. La palabra suelta "entrega" es ambigua (puede ser dirección/horario),
	# así que solo cuenta si aparece en una frase de envío.
	if _frase_clave_en_texto(t, _DELIVERY_KEYWORDS):
		tipo_entrega = "delivery"
	elif _frase_clave_en_texto(t, _PICKUP_KEYWORDS):
		tipo_entrega = "pickup"

	if _frase_clave_en_texto(t, _PAGO_MOVIL_KEYWORDS):
		metodo_pago = "pago movil"
	elif "efectivo" in t:
		metodo_pago = "efectivo"

	return tipo_entrega, metodo_pago


def _ejemplo_pedido(catalog: list[dict] | None) -> str:
	"""Construye un ejemplo de pedido usando un producto real del catálogo."""

	if catalog:
		producto = catalog[0].get("nombre_producto", "producto")
		return f"quiero 6 {producto} para delivery"
	return "quiero 6 productos para delivery"


def _formatear_catalogo(productos: list[dict], incluir_stock: bool = False, mostrar_etiquetas: bool = False) -> str:
	"""Devuelve un listado breve de productos disponibles."""

	if not productos:
		return "No hay productos disponibles en este momento."

	lineas = []
	for producto in productos:
		nombre = producto.get("nombre_producto", "producto")
		precio_detal = float(producto.get("precio_detal", 0) or 0)
		precio_mayor = float(producto.get("precio_mayor", precio_detal) or precio_detal)
		stock = producto.get("cantidad", 0)
		etiq = etiquetas_resumen_linea(producto.get("etiquetas") or []) if mostrar_etiquetas else ""
		if incluir_stock:
			lineas.append(f"- {nombre} | ${precio_detal:.2f} (mayor ${precio_mayor:.2f}) | stock: {stock}{etiq}")
		else:
			lineas.append(f"- {nombre} | ${precio_detal:.2f} (mayor ${precio_mayor:.2f}){etiq}")

	return "Estos son los productos disponibles:\n" + "\n".join(lineas)


def _dividir_mensaje(texto: str, limite: int = 3500) -> list[str]:
	"""Divide un mensaje largo en bloques seguros para Telegram."""

	texto = (texto or "").strip()
	if not texto:
		return []
	if len(texto) <= limite:
		return [texto]

	lineas = texto.splitlines()
	partes: list[str] = []
	actual = ""
	for linea in lineas:
		if not actual:
			actual = linea
		elif len(actual) + 1 + len(linea) <= limite:
			actual += "\n" + linea
		else:
			partes.append(actual)
			actual = linea
	if actual:
		partes.append(actual)
	return partes


async def _enviar_catalogo(
	update: Update,
	encabezado: str,
	productos: list[dict] | tuple[list[dict], str],
	*,
	mostrar_etiquetas: bool = False,
) -> None:
	"""Envía el catálogo completo en uno o varios mensajes."""

	# por compatibilidad _enviar_catalogo puede aceptar que 'productos' sea una tupla
	# (lista_productos, modo_precio) si se quiere renderizar solo un modo.
	modo_precio = None
	if isinstance(productos, tuple) and len(productos) == 2:
		productos, modo_precio = productos

	msg = update.effective_message
	if msg is None:
		return

	if await _enviar_catalogo_visual_si_disponible(update, modo_precio=modo_precio, encabezado=encabezado):
		return

	# Sin imagen: no enviar lista larga de precios (prioridad visual acordada).
	_log.info(
		"Catálogo visual no encontrado; se omite lista en texto. "
		"Coloca PNG/JPEG en IA_Chatbot/assets/ o define CATALOGO_VISUAL_PATH en .env"
	)
	await msg.reply_text(
		"No encontré la imagen del catálogo REF en el servidor, por eso no te envío la lista larga de precios.\n\n"
		"Qué hacer: en la carpeta del bot ya existe la carpeta «assets». Guarda ahí tu foto "
		"(por ejemplo catalogo_helados_cali.png o tu archivo JPEG de WhatsApp) "
		"o agrega en .env la variable CATALOGO_VISUAL_PATH con la ruta completa al archivo.\n\n"
		"Reinicia el bot y al elegir detal o mayor solo recibirás la imagen.\n\n"
		"Mientras tanto, escribe el producto que quieres y te confirmo precio y stock."
	)


def _formatear_precio_producto(producto: dict, modo_precio: str | None = None) -> str:
	"""Devuelve el precio legible según el modo de compra activo."""

	nombre = producto.get("nombre_producto", "producto")
	precio_detal = float(producto.get("precio_detal", 0) or 0)
	precio_mayor = float(producto.get("precio_mayor", precio_detal) or precio_detal)
	if modo_precio == "mayor":
		return f"{nombre}: ${precio_mayor:.2f} al mayor (tu modo actual)."
	if modo_precio == "detal":
		return f"{nombre}: ${precio_detal:.2f} al detal (tu modo actual)."
	return f"{nombre}: ${precio_detal:.2f} al detal y ${precio_mayor:.2f} al mayor."


def _formatear_sugerencias_productos(productos: list[str]) -> str:
	"""Construye un mensaje corto con productos similares."""

	if not productos:
		return ""
	return "Productos similares: " + ", ".join(productos[:5])


def _extract_support_reference(text: str) -> int | None:
	"""Busca un id de pedido en texto libre para soporte."""

	match = re.search(r"(?:pedido\s*#?\s*|#)(\d+)", text.lower())
	if not match:
		return None
	try:
		return int(match.group(1))
	except ValueError:
		return None


def _looks_like_support_message(text: str) -> bool:
	"""Confirma soporte explícito para evitar falsos positivos de intención."""

	if not text:
		return False
	if _extract_support_reference(text) is not None:
		return True
	return bool(
		re.search(
			r"(?i)\b(soporte|reclamo|queja|problema|incidencia|pedido\s*#\s*\d+)\b",
			text,
		)
	) or bool(re.search(r"(?i)\bayuda\b.*\bpedido\b", text)) or tiene_senal_negativa_es(text)


def _extract_location_from_text(text: str) -> str | None:
	"""Intenta extraer una ubicación escrita desde texto libre."""

	raw = (text or "").strip()
	if not raw:
		return None

	normalized = normalize_text(raw)
	patterns = [
		r"(?:direccion|direccion de entrega|ubicacion|ubicacion de entrega)\s*[:\-]?\s*(.+)",
		r"(?:delivery|envio|enviar)\s+(?:a|hasta)\s+(.+)",
		r"(?:entrega(?:r)?(?:lo)?\s+en)\s+(.+)",
	]

	for pattern in patterns:
		match = re.search(pattern, normalized)
		if match:
			value = match.group(1).strip(" .,")
			value = re.sub(r"\b(?:delivery|pickup|pago movil|pago movil|efectivo)\b.*$", "", value).strip(" .,")
			if len(value) >= 8:
				return value

	return None


@dataclass
class _OrderParseResult:
	"""Resultado canónico del parser de pedido antes de mutar user_data."""

	items: list[dict] = field(default_factory=list)
	ambiguos: list[dict] = field(default_factory=list)
	modo_precio: str | None = None
	tipo_entrega: str | None = None
	metodo_pago: str | None = None
	ubicacion_entrega: str | None = None
	unknown_terms: list[str] = field(default_factory=list)
	remove_product_query: str | None = None

	@property
	def has_items_or_pending(self) -> bool:
		return bool(self.items or self.ambiguos)

	@property
	def has_slots(self) -> bool:
		return bool(self.modo_precio or self.tipo_entrega or self.metodo_pago or self.ubicacion_entrega)


def _texto_agrega_producto_explicito(texto: str) -> bool:
	"""Detecta intención explícita de sumar producto, útil cuando el flujo espera otro slot."""

	tn = normalize_text(texto)
	if not tn:
		return False
	return bool(
		re.search(
			r"\b(agrega|agregar|anade|anadir|añade|añadir|tambien quiero|tambien dame|tambien agrega|"
			r"también quiero|también dame|también agrega|sumale|suma|incluye|y quiero|ah y|ademas|además)\b",
			tn,
		)
	)


def _texto_debe_tratarse_como_direccion(texto: str, estado: int | None) -> bool:
	"""En estado de ubicación el texto es dirección, salvo que sea un comando claro."""

	if estado != PIDIENDO_UBICACION:
		return False
	if not texto:
		return False
	if _texto_pide_catalogo(texto) or _texto_es_cancelacion(texto) or _texto_editar_ubicacion(texto):
		return False
	if _extract_delivery_and_payment(texto) != (None, None) or _modo_precio_desde_texto(texto):
		return False
	if _texto_afirmativo(texto) or _texto_negativo(texto):
		return False
	return not _texto_agrega_producto_explicito(texto)


def _strip_location_clause_for_item_parse(texto: str) -> str:
	"""Quita frases de ubicación para que calles/urbanizaciones no se traten como productos."""

	raw = (texto or "").strip()
	if not raw:
		return ""
	patterns = (
		r"(?:direccion|direccion de entrega|ubicacion|ubicacion de entrega)\s*[:\-]?\s*.+$",
		r"(?:delivery|envio|enviar)\s+(?:a|hasta)\s+.+$",
		r"(?:entrega(?:r)?(?:lo)?\s+en)\s+.+$",
	)
	out = normalize_text(raw)
	for pattern in patterns:
		out = re.sub(pattern, " ", out, flags=re.IGNORECASE).strip()
	out = re.sub(
		r"\b(?:pago movil|pago móvil|pago efectivo|pago|efectivo|pickup|pick up|delivery|domicilio|envio)\b",
		" ",
		out,
	)
	return re.sub(r"\s+", " ", out).strip()


_NEG_REMOCION_FRAG_INVALIDOS = frozenset(
	normalize_text(x)
	for x in (
		"mas",
		"más",
		"nada",
		"mas nada",
		"nada mas",
		"nada más",
		"productos",
		"helados",
		"eso",
		"esto",
	)
)

_NEG_REMOCION_CLAUSULAS: tuple[re.Pattern[str], ...] = (
	re.compile(r"(?i)\s+y\s+ya\s+no\s+quiero\s+(.+)$"),
	re.compile(r"(?i)\s*,\s*ya\s+no\s+quiero\s+(.+)$"),
	re.compile(r"(?i)^ya\s+no\s+quiero\s+(.+)$"),
	re.compile(r"(?i)\s+y\s+no\s+quiero\s+(.+)$"),
	re.compile(r"(?i)^no\s+quiero\s+(.+)$"),
	re.compile(r"(?i)^(?:quita|quitar|saca|sacar|borra|borrar|elimina|eliminar|resta|restar)\s+(.+)$"),
	re.compile(r"(?i)\b(?:saca|quita|borra|elimina)\s+(?:el|la|los|las)\s+(.+)$"),
)


def _fragmento_remocion_valido(frag: str) -> bool:
	f = (frag or "").strip()
	if len(f) < 2:
		return False
	nf = normalize_text(f)
	if nf in _NEG_REMOCION_FRAG_INVALIDOS:
		return False
	return True


def _normalizar_fragmento_producto_remocion(frag: str) -> str:
	return re.sub(r"^(el|la|los|las|un|una|unos|unas)\s+", "", (frag or "").strip(), flags=re.IGNORECASE).strip()


def _extraer_fragmento_remocion_por_negacion(texto: str) -> str | None:
	raw = (texto or "").strip()
	if not raw:
		return None
	for pat in _NEG_REMOCION_CLAUSULAS:
		m = pat.search(raw)
		if not m:
			continue
		frag = _normalizar_fragmento_producto_remocion(m.group(1) or "")
		if not _fragmento_remocion_valido(frag):
			continue
		return frag
	return None


def _strip_clausulas_remocion_desde_texto(texto: str) -> str:
	out = (texto or "").strip()
	if not out:
		return ""
	for _ in range(8):
		matched = False
		for pat in _NEG_REMOCION_CLAUSULAS:
			m = pat.search(out)
			if not m:
				continue
			frag = _normalizar_fragmento_producto_remocion(m.group(1) or "")
			if not _fragmento_remocion_valido(frag):
				continue
			out = (out[: m.start()] + " " + out[m.end() :]).strip()
			matched = True
			break
		if not matched:
			break
	return re.sub(r"\s+", " ", out).strip()


def _parse_order_message(texto: str, catalog: list[dict], estado: int | None = None) -> _OrderParseResult:
	"""Parser determinista: items primero, slots explícitos después, con guardas por estado."""

	result = _OrderParseResult()
	if not texto:
		return result

	result.modo_precio = _modo_precio_desde_texto(texto)
	result.tipo_entrega, result.metodo_pago = _extract_delivery_and_payment(texto)

	if _texto_debe_tratarse_como_direccion(texto, estado):
		result.ubicacion_entrega = texto.strip()
		return result

	loc = _extract_location_from_text(texto)
	if loc:
		result.ubicacion_entrega = loc

	item_text = _strip_location_clause_for_item_parse(texto)
	if not item_text:
		item_text = texto
	item_text = strip_control_commands_for_product_search(item_text)
	rem_frag = _extraer_fragmento_remocion_por_negacion(item_text)
	parse_items_text = _strip_clausulas_remocion_desde_texto(item_text) if rem_frag else item_text
	if rem_frag:
		result.remove_product_query = rem_frag
	if parse_items_text:
		items, ambiguos = _extract_items_from_text(parse_items_text, catalog)
		items_auto, pending_unknowns = _build_pending_unknown_items_from_text(parse_items_text, catalog)
	else:
		items, ambiguos = [], []
		items_auto, pending_unknowns = [], []

	merged: dict[str, int] = {}
	for item in [*items, *items_auto]:
		producto = str(item.get("producto", "")).strip()
		cantidad = int(item.get("cantidad", 0) or 0)
		if producto and cantidad > 0:
			merged[producto] = merged.get(producto, 0) + cantidad
	result.items = [{"producto": producto, "cantidad": cantidad} for producto, cantidad in merged.items()]
	result.ambiguos = [*ambiguos, *pending_unknowns]
	result.unknown_terms = list_unknown_product_terms(parse_items_text, catalog) if parse_items_text else []

	_log.debug(
		"parse_order_message items=%s ambiguos=%s modo=%s entrega=%s pago=%s ubicacion=%s unknown=%s",
		result.items,
		len(result.ambiguos),
		result.modo_precio,
		result.tipo_entrega,
		result.metodo_pago,
		bool(result.ubicacion_entrega),
		result.unknown_terms,
	)
	return result


def _aplicar_parse_slots(context: ContextTypes.DEFAULT_TYPE, parsed: _OrderParseResult) -> None:
	"""Aplica slots parseados sin tocar el carrito."""

	if parsed.modo_precio in {"detal", "mayor"} and context.user_data.get("modo_precio") is None:
		context.user_data["modo_precio"] = parsed.modo_precio
	if parsed.tipo_entrega in {"delivery", "pickup"}:
		context.user_data["tipo_entrega"] = parsed.tipo_entrega
		if parsed.tipo_entrega == "pickup":
			context.user_data.setdefault("metodo_pago", "presencial")
			context.user_data["ubicacion_entrega"] = None
			context.user_data.pop("comprobante_file_id", None)
		elif context.user_data.get("metodo_pago") == "presencial":
			context.user_data["metodo_pago"] = None
	if parsed.metodo_pago in {"efectivo", "pago movil"}:
		context.user_data["metodo_pago"] = parsed.metodo_pago
	if parsed.ubicacion_entrega and context.user_data.get("tipo_entrega") == "delivery":
		context.user_data["ubicacion_entrega"] = parsed.ubicacion_entrega


async def _procesar_slots_globales_sin_items(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
	tid: int,
) -> int | None:
	"""Aplica cambios de modo/entrega/pago aunque el flujo esté esperando otro dato."""

	if _texto_tiene_items_o_producto_pedido(texto, catalog):
		return None

	parsed = _parse_order_message(texto, catalog, None)
	cambio = False
	partes: list[str] = []

	if parsed.modo_precio in {"detal", "mayor"}:
		modo_anterior = context.user_data.get("modo_precio")
		if modo_anterior != parsed.modo_precio:
			context.user_data["modo_precio"] = parsed.modo_precio
			await _recalcular_items_guardados_si_hay_carrito(update, context)
			label = "al mayor" if parsed.modo_precio == "mayor" else "al detal"
			partes.append(f"Listo, usaré precio {label}.")
			cambio = True

	if parsed.tipo_entrega in {"delivery", "pickup"}:
		if context.user_data.get("tipo_entrega") != parsed.tipo_entrega:
			context.user_data["tipo_entrega"] = parsed.tipo_entrega
			context.user_data.pop("comprobante_file_id", None)
			context.user_data.pop("esperando_comprobante", None)
			if parsed.tipo_entrega == "pickup":
				context.user_data["metodo_pago"] = "presencial"
				context.user_data["ubicacion_entrega"] = None
				_reset_cotizacion_delivery(context, tid)
				partes.append("Listo, será pickup.")
			else:
				if context.user_data.get("metodo_pago") == "presencial":
					context.user_data["metodo_pago"] = None
				context.user_data.pop("ubicacion_entrega", None)
				_reset_cotizacion_delivery(context, tid)
				partes.append("Listo, será delivery.")
			cambio = True

	if parsed.metodo_pago in {"efectivo", "pago movil"} and context.user_data.get("metodo_pago") != parsed.metodo_pago:
		context.user_data["metodo_pago"] = parsed.metodo_pago
		partes.append(f"Listo, método de pago: {parsed.metodo_pago}.")
		cambio = True

	if not cambio:
		return None

	estado = _infer_estado_tras_volver_al_chat(context, tid)
	msg = update.effective_message
	if msg:
		if not _items_desde_context(context) and context.user_data.get("producto") is None:
			await msg.reply_text(" ".join(partes) + "\n\nAhora dime qué productos quieres pedir.")
			return PIDIENDO_PRODUCTO
		if estado == PIDIENDO_CANTIDAD:
			await msg.reply_text(" ".join(partes) + "\n\nAhora dime la cantidad del producto.")
			return PIDIENDO_CANTIDAD
		if estado == PIDIENDO_UBICACION:
			await msg.reply_text(
				" ".join(partes) + "\n\nEnvíame la dirección o comparte la ubicación para calcular el delivery."
			)
			return PIDIENDO_UBICACION
		if estado == PIDIENDO_TIPO_ENTREGA:
			await msg.reply_text(" ".join(partes) + "\n\n¿El pedido será delivery o pickup?")
			return PIDIENDO_TIPO_ENTREGA
		if estado == PIDIENDO_METODO_PAGO:
			await msg.reply_text(" ".join(partes) + "\n\nElige efectivo o pago móvil.")
			return PIDIENDO_METODO_PAGO

	return await _continuar_pedido_tras_carrito(update, context)


async def _procesar_items_globales_en_estado(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
	*,
	mensaje_siguiente: str,
	estado_siguiente: int,
) -> int | None:
	"""Permite agregar productos aunque el flujo esté esperando otro parámetro."""

	if not _texto_tiene_items_o_producto_pedido(texto, catalog):
		return None
	parsed = _parse_order_message(texto, catalog, PIDIENDO_PRODUCTO)
	if parsed.items:
		try:
			await _sumar_items_al_carrito(update, context, parsed.items)
		except ValueError as exc:
			await update.effective_message.reply_text(str(exc))
			return PIDIENDO_PRODUCTO
		_aplicar_parse_slots(context, parsed)
	if parsed.ambiguos:
		context.user_data["items_pendientes_clarificar"] = parsed.ambiguos
		await update.effective_message.reply_text(_formatear_item_ambiguo(parsed.ambiguos[0], 1, len(parsed.ambiguos)))
		return PIDIENDO_PRODUCTO
	if parsed.items:
		await update.effective_message.reply_text("Listo, actualicé los productos.\n\n" + mensaje_siguiente)
		return estado_siguiente
	return None


def _estado_nombre(estado: int | None) -> str:
	return {
		PIDIENDO_MODO_PRECIO: "pidiendo_modo_precio",
		PIDIENDO_PRODUCTO: "pidiendo_producto",
		PIDIENDO_CANTIDAD: "pidiendo_cantidad",
		PIDIENDO_TIPO_ENTREGA: "pidiendo_tipo_entrega",
		PIDIENDO_UBICACION: "pidiendo_ubicacion",
		ESPERANDO_COSTO_DELIVERY: "esperando_costo_delivery",
		PIDIENDO_METODO_PAGO: "pidiendo_metodo_pago",
		PIDIENDO_COMPROBANTE: "pidiendo_comprobante",
		CONFIRMANDO_PEDIDO: "confirmando_pedido",
	}.get(estado, "conversacion_general")


def _contexto_pedido_para_modelo(context: ContextTypes.DEFAULT_TYPE) -> dict:
	return {
		"modo_precio": context.user_data.get("modo_precio"),
		"items": _items_desde_context(context),
		"producto_actual": context.user_data.get("producto"),
		"cantidad_actual": context.user_data.get("cantidad"),
		"tipo_entrega": context.user_data.get("tipo_entrega"),
		"ubicacion_entrega": context.user_data.get("ubicacion_entrega"),
		"metodo_pago": context.user_data.get("metodo_pago"),
		"pendientes_clarificar": context.user_data.get("items_pendientes_clarificar", []),
		"esperando_comprobante": bool(context.user_data.get("esperando_comprobante")),
	}


async def _procesar_interpretacion_modelo(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	texto: str,
	catalog: list[dict],
	tid: int,
) -> int | None:
	"""Fallback con Gemini: interpreta lenguaje natural y aplica solo acciones validadas."""

	msg = update.effective_message
	if msg is None or not texto.strip():
		return None

	estado_actual = _infer_estado_tras_volver_al_chat(context, tid)
	data = interpretar_conversacion_pedido(
		texto,
		estado=_estado_nombre(estado_actual),
		contexto_pedido=_contexto_pedido_para_modelo(context),
		catalogo=catalog,
	)
	if not data or float(data.get("confidence", 0) or 0) < 0.55:
		return None

	intent = str(data.get("intent") or "unknown").strip().lower()
	if intent == "confirm" and estado_actual == CONFIRMANDO_PEDIDO:
		return await confirmar_pedido(update, context)
	if intent in {"deny", "cancel"} and estado_actual == CONFIRMANDO_PEDIDO:
		return await cancelar_pedido(update, context)
	if intent == "cancel":
		return await cancelar_pedido(update, context)

	aplico_algo = False
	modo = data.get("price_mode")
	if modo in {"detal", "mayor"}:
		if context.user_data.get("modo_precio") != modo:
			context.user_data["modo_precio"] = modo
			await _recalcular_items_guardados_si_hay_carrito(update, context)
			aplico_algo = True

	entrega = data.get("delivery_type")
	if entrega in {"delivery", "pickup"}:
		if entrega != context.user_data.get("tipo_entrega"):
			context.user_data["tipo_entrega"] = entrega
			context.user_data.pop("comprobante_file_id", None)
			context.user_data.pop("esperando_comprobante", None)
			if entrega == "pickup":
				context.user_data["metodo_pago"] = "presencial"
				context.user_data["ubicacion_entrega"] = None
				_reset_cotizacion_delivery(context, tid)
			else:
				if context.user_data.get("metodo_pago") == "presencial":
					context.user_data["metodo_pago"] = None
				context.user_data.pop("ubicacion_entrega", None)
				_reset_cotizacion_delivery(context, tid)
			aplico_algo = True

	pago = data.get("payment_method")
	if pago in {"efectivo", "pago movil", "presencial"}:
		context.user_data["metodo_pago"] = pago
		aplico_algo = True

	loc = str(data.get("location") or "").strip()
	if loc and context.user_data.get("tipo_entrega") == "delivery":
		context.user_data["ubicacion_entrega"] = loc
		aplico_algo = True

	resueltos: list[dict] = []
	pendientes: list[dict] = []
	for raw_item in data.get("products") or []:
		if not isinstance(raw_item, dict):
			continue
		raw_name = str(raw_item.get("name") or "").strip()
		if not raw_name:
			continue
		try:
			qty = max(1, int(raw_item.get("quantity") or 1))
		except (TypeError, ValueError):
			qty = 1
		producto, sugerencias = _resolver_producto_desde_texto(raw_name, catalog)
		if producto:
			resueltos.append({"producto": producto, "cantidad": qty})
		else:
			pendientes.append(
				{
					"tipo": "desconocido",
					"segmento": raw_name,
					"cantidad": qty,
					"candidatos": sugerencias[:5],
				}
			)

	for raw_item in data.get("ambiguous_products") or []:
		if not isinstance(raw_item, dict):
			continue
		try:
			qty = max(1, int(raw_item.get("quantity") or 1))
		except (TypeError, ValueError):
			qty = 1
		candidates = [str(c).strip() for c in (raw_item.get("candidates") or []) if str(c).strip()]
		text = str(raw_item.get("text") or raw_item.get("name") or "producto").strip()
		pendientes.append(
			{
				"tipo": "desconocido",
				"segmento": text,
				"cantidad": qty,
				"candidatos": candidates[:5],
			}
		)

	if resueltos:
		try:
			await _sumar_items_al_carrito(update, context, resueltos)
		except ValueError as exc:
			await msg.reply_text(str(exc))
			return PIDIENDO_PRODUCTO
		aplico_algo = True

	for raw_remove in data.get("remove_products") or []:
		if not isinstance(raw_remove, dict):
			continue
		name = str(raw_remove.get("name") or "").strip()
		if name:
			estado_rem = await _remover_item_del_carrito_por_fragmento(update, context, name, catalog)
			if estado_rem is not None:
				aplico_algo = True

	if pendientes:
		context.user_data["items_pendientes_clarificar"] = pendientes
		await msg.reply_text(_formatear_item_ambiguo(pendientes[0], 1, len(pendientes)))
		return PIDIENDO_PRODUCTO

	if aplico_algo:
		return await _continuar_pedido_tras_carrito(update, context)

	if intent in {"smalltalk", "unknown"}:
		reply = str(data.get("reply") or "").strip()
		if reply:
			await msg.reply_text(reply)
			return _infer_estado_tras_volver_al_chat(context, tid) if _pedido_en_curso(context) else ConversationHandler.END

	return None


def _extract_items_from_text(text: str, catalog: list[dict]) -> tuple[list[dict], list[dict]]:
	"""Intenta extraer varios items y señala los casos ambiguos.

	Devuelve (items, ambiguos), donde:
	- items: [{'producto': nombre_producto, 'cantidad': cantidad}]
	- ambiguos: [{'segmento': str, 'cantidad': int, 'candidatos': [..]}]
	"""
	text = strip_control_commands_for_product_search(text or "")
	_orphan_qty_skip = frozenset(
		normalize_text(w)
		for w in (
			"pedido",
			"orden",
			"carrito",
			"anexo",
			"anexa",
			"anexar",
			"agrega",
			"agregar",
			"para",
			"llevar",
			"delivery",
			"pickup",
			"tambien",
			"mas",
			"unas",
			"unos",
			"quiero",
			"dame",
			"necesito",
		)
	)

	if not text or not catalog:
		return [], []

	raw_text = text.strip()
	normalized = apply_colloquial_helado_terms(normalize_text(raw_text))
	# Mapear aliases a producto real usando la misma heuristica del NLU.
	alias_map: dict[str, set[str]] = {}
	exact_names: set[str] = set()
	for producto in catalog:
		nombre = producto.get("nombre_producto")
		if not nombre:
			continue
		exact_names.add(normalize_text(nombre))
		for alias in _build_product_aliases(nombre):
			alias_map.setdefault(alias, set()).add(nombre)
		for alias in _etiqueta_aliases_from_product(producto):
			if len(alias) < 4:
				continue
			alias_map.setdefault(alias, set()).add(nombre)

	num_words = _NUM_WORDS if "_NUM_WORDS" in globals() else {}
	number_token = r"(?:\d+|" + "|".join(re.escape(word) for word in num_words.keys()) + r")"
	_qty_word_tail = re.compile(rf"(?<!\w)({number_token})\s+([\w]{{3,}})(?!\w)", re.IGNORECASE)
	segmentos_raw = _split_order_segments(raw_text)
	segmentos = [apply_colloquial_helado_terms(normalize_text(segmento)) for segmento in segmentos_raw]
	if not segmentos:
		segmentos = [normalized]

	items: dict[str, int] = {}
	ambiguedades: list[dict] = []

	def _parse_qty(token: str | None) -> int | None:
		if not token:
			return None
		if token.isdigit():
			return int(token)
		return num_words.get(token)

	for segmento in segmentos:
		segmento = segmento.strip()
		if not segmento:
			continue
		cantidad_segmento, fragmento_segmento = _parse_segment_qty_and_name(segmento)
		segmento_mostrar = fragmento_segmento or segmento

		# Primer intento por segmento completo: evita que aliases cortos ("chicle") ganen
		# antes que la línea real ("cono chicle") y pierdan la cantidad local.
		if fragmento_segmento and _tokens_producto(fragmento_segmento):
			ranked_segmento = _rank_catalog_candidates(fragmento_segmento, catalog, limit=5)
			producto_segmento = _select_unique_catalog_candidate(
				ranked_segmento,
				strong_score=0.86,
				min_gap=0.08,
				allow_loose_single=False,
			)
			if producto_segmento:
				if _fragmento_tiene_sobrantes_no_cubiertos(fragmento_segmento, producto_segmento):
					ambiguedades.append(
						{
							"segmento": segmento_mostrar,
							"cantidad": max(1, int(cantidad_segmento)),
							"candidatos": [str(item["producto"]) for item in ranked_segmento[:5]],
						}
					)
					continue
				items[producto_segmento] = items.get(producto_segmento, 0) + max(1, int(cantidad_segmento))
				continue
			if _fragmento_parece_producto_unico_incompleto(fragmento_segmento):
				ambiguedades.append(
					{
						"segmento": segmento_mostrar,
						"cantidad": max(1, int(cantidad_segmento)),
						"candidatos": [str(item["producto"]) for item in ranked_segmento[:5]],
					}
				)
				continue

		hits: list[tuple[int, int, str, list[str]]] = []
		for alias, product_name in alias_map.items():
			if len(alias) < 4:
				continue
			for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", segmento):
				hits.append((match.start(), match.end(), alias, sorted(product_name)))

		if not hits:
			selected = []
			occupied: list[tuple[int, int]] = []
		else:
			# Evitar que alias cortos vuelvan a contar el mismo producto: quedarse con spans no solapados,
			# priorizando los aliases más largos.
			hits.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
			selected = []
			occupied = []
			for start, end, alias, product_names in hits:
				if any(not (end <= left or start >= right) for left, right in occupied):
					continue
				selected.append((start, end, alias, product_names))
				occupied.append((start, end))

		for start, end, alias, product_names in selected:
			window_left = segmento[max(0, start - 48):start].strip()
			window_right = segmento[end : min(len(segmento), end + 28)].strip()

			qty = None
			left_match = re.search(rf"({number_token})\s*$", window_left)
			if left_match:
				qty = _parse_qty(left_match.group(1))
			if qty is None:
				right_match = re.match(rf"^{number_token}", window_right)
				if right_match:
					qty = _parse_qty(right_match.group(0))

			if qty is None:
				qty = 1

			cantidad = max(1, int(qty))

			# Si el alias apunta a varios productos o el texto es demasiado corto,
			# devolver candidatos para que el handler pida aclaración en vez de elegir mal.
			if len(product_names) > 1 and alias not in exact_names:
				ambiguedades.append(
					{
						"segmento": segmento_mostrar,
						"cantidad": cantidad,
						"candidatos": product_names[:5],
					}
				)
				continue

			producto_nombre = product_names[0]
			items[producto_nombre] = items.get(producto_nombre, 0) + cantidad

		# Pares "20 conos" lejos de otros alias o plural distinto: no solapar spans ya usados.
		if _qty_word_tail is not None:
			for m in _qty_word_tail.finditer(segmento):
				if any(not (m.end() <= left or m.start() >= right) for left, right in occupied):
					continue
				word_raw = (m.group(2) or "").strip()
				word_norm = normalize_text(word_raw)
				if len(word_norm) < 3 or word_norm in _orphan_qty_skip:
					continue
				resolved: set[str] = set()
				for variant in {word_norm, *_spanish_token_variants(word_norm)}:
					if len(variant) < 3:
						continue
					if variant in alias_map:
						resolved |= alias_map[variant]
				if len(resolved) > 1:
					qty_o = _parse_qty(m.group(1))
					if qty_o is None:
						qty_o = 1
					ambiguedades.append(
						{
							"segmento": segmento_mostrar or word_raw,
							"cantidad": max(1, int(qty_o)),
							"candidatos": sorted(resolved)[:5],
						}
					)
					continue
				if len(resolved) == 1:
					qty_o = _parse_qty(m.group(1))
					if qty_o is None:
						qty_o = 1
					producto_nombre = next(iter(resolved))
					items[producto_nombre] = items.get(producto_nombre, 0) + max(1, int(qty_o))

	ambiguedades_dedup: dict[tuple[str, tuple[str, ...]], dict] = {}
	for item in ambiguedades:
		key = (item.get("segmento", ""), tuple(item.get("candidatos", [])))
		actual = ambiguedades_dedup.get(key)
		if actual is None or int(item.get("cantidad", 0)) > int(actual.get("cantidad", 0)):
			ambiguedades_dedup[key] = item

	return ([{"producto": nombre, "cantidad": cantidad} for nombre, cantidad in items.items()], list(ambiguedades_dedup.values()))


def _preparar_items_guardados(items: list[dict], modo_precio: str | None = None) -> list[dict]:
	"""Resuelve nombre/cantidad a IDs y precios unitarios para persistir el pedido.

	`modo_precio` puede ser 'detal' o 'mayor' y se usa para fijar `precio_unitario`.
	"""

	items_guardados: list[dict] = []
	for item in items:
		producto_nombre = str(item.get("producto", "")).strip()
		cantidad = int(item.get("cantidad", 0))
		if not producto_nombre:
			raise ValueError("No identifiqué un producto válido en tu pedido.")
		if cantidad <= 0:
			raise ValueError("La cantidad de cada producto debe ser mayor que cero.")

		producto_db = obtener_producto_disponible_por_nombre(producto_nombre)
		if producto_db is None:
			raise ValueError(f"No encontré '{producto_nombre}' con stock disponible. Intenta con otro producto.")
		if cantidad > int(producto_db["cantidad"]):
			raise ValueError(f"La cantidad de {producto_db['nombre_producto']} debe ser entre 1 y {producto_db['cantidad']}.")

		precio_unitario = float(
			obtener_resumen_montos(
				producto_db["nombre_producto"],
				cantidad,
				delivery_costo_usd=0,
				modo_precio=modo_precio,
				incluir_bcv=False,
			)["precio_usd"]
		)
		items_guardados.append(
			{
				"producto_id": producto_db["id"],
				"producto": producto_db["nombre_producto"],
				"cantidad": cantidad,
				"precio_unitario": precio_unitario,
			}
		)

	return items_guardados


def _formatear_items_ambiguos(ambiguos: list[dict]) -> str:
	"""Construye un mensaje breve para pedir aclaración de items ambiguos."""

	if not ambiguos:
		return ""

	lineas = []
	for item in ambiguos[:3]:
		candidatos = item.get("candidatos", [])
		cantidad = item.get("cantidad", 1)
		lineas.append(f"- «{item.get('segmento', 'producto')}» x{cantidad}: {', '.join(candidatos[:4])}")

	return (
		"No reconocí con certeza qué línea del catálogo quisiste con esto:\n"
		+ "\n".join(lineas)
		+ "\n\n"
		+ _texto_recordatorio_nombre_catalogo()
	)


def _formatear_item_ambiguo(item: dict, posicion: int, total: int) -> str:
	"""Construye un mensaje de aclaración para un solo producto ambiguo."""

	tipo = item.get("tipo") or "ambiguo"
	candidatos = item.get("candidatos", [])
	segmento = item.get("segmento", "producto")
	cantidad = item.get("cantidad", 1)
	lista_candidatos = "\n".join(f"- {candidato}" for candidato in candidatos[:5]) if candidatos else ""
	if tipo == "desconocido":
		mensaje = (
			f"No reconocí bien el producto {posicion} de {total} que escribiste "
			f"(«{segmento}» x{cantidad}).\n"
		)
		if lista_candidatos:
			mensaje += f"Las opciones más parecidas del catálogo son:\n{lista_candidatos}\n\n"
		mensaje += (
			"Respóndeme con el nombre correcto. Si con una sola palabra basta para aclararlo, también sirve.\n"
			+ _texto_recordatorio_nombre_catalogo()
		)
		return mensaje
	prefijo = ""
	return (
		prefijo
		+ f"No reconocí con certeza el producto {posicion} de {total} en lo que escribiste "
		f"(«{segmento}» x{cantidad}).\n"
		f"Opciones parecidas en el catálogo:\n{lista_candidatos}\n\n"
		"Puedes responder con solo la parte que falta si eso lo aclara (por ejemplo: "
		"**mantecado** o **paleta**).\n"
		+ _texto_recordatorio_nombre_catalogo()
	)


def _resolver_items_ambiguos(texto: str, ambiguos: list[dict], catalog: list[dict]) -> tuple[list[dict], list[dict]]:
	"""Intenta resolver una o más aclaraciones de productos ambiguos."""

	if not ambiguos:
		return [], []

	normalized = normalize_text(texto)
	resolved: list[dict] = []
	remaining: list[dict] = []
	for item in ambiguos:
		tipo_item = item.get("tipo") or "ambiguo"
		candidatos = item.get("candidatos", [])
		cantidad = int(item.get("cantidad", 1))
		producto, nuevos_candidatos = _resolver_producto_desde_texto(texto, catalog, candidatos=candidatos or None)
		if producto and (not candidatos or producto in candidatos):
			resolved.append({"producto": producto, "cantidad": cantidad})
			continue

		if tipo_item == "desconocido":
			producto_catalogo, _ = _resolver_producto_desde_texto(texto, catalog, candidatos=None)
			if producto_catalogo:
				resolved.append({"producto": producto_catalogo, "cantidad": cantidad})
				continue

		if candidatos:
			coincidencia_exacta = next((candidato for candidato in candidatos if normalize_text(candidato) in normalized), None)
			if coincidencia_exacta:
				resolved.append({"producto": coincidencia_exacta, "cantidad": cantidad})
				continue

		item_actualizado = dict(item)
		if nuevos_candidatos:
			item_actualizado["candidatos"] = nuevos_candidatos
		remaining.append(item_actualizado)

	return resolved, remaining


def _resolver_un_item_ambiguo(texto: str, item: dict, catalog: list[dict]) -> tuple[dict | None, dict | None]:
	"""Intenta resolver un único item ambiguo a partir de la respuesta del usuario."""

	if not item:
		return None, None

	tipo_item = item.get("tipo") or "ambiguo"
	normalized = normalize_text(texto)
	candidatos = item.get("candidatos", [])
	cantidad = int(item.get("cantidad", 1))
	producto_detectado, nuevos_candidatos = _resolver_producto_desde_texto(
		texto,
		catalog,
		candidatos=candidatos or None,
	)
	if producto_detectado and (not candidatos or producto_detectado in candidatos):
		return {"producto": producto_detectado, "cantidad": cantidad}, None

	if tipo_item == "desconocido":
		producto_catalogo, _ = _resolver_producto_desde_texto(texto, catalog, candidatos=None)
		if producto_catalogo:
			return {"producto": producto_catalogo, "cantidad": cantidad}, None

	for candidato in candidatos:
		if normalize_text(candidato) in normalized:
			return {"producto": candidato, "cantidad": cantidad}, None

	# Si no se pudo resolver, mantener el mismo item pendiente.
	item_actualizado = dict(item)
	if nuevos_candidatos:
		item_actualizado["candidatos"] = nuevos_candidatos
	return None, item_actualizado


async def _notify_admin_delivery_location(
	update: Update,
	context: ContextTypes.DEFAULT_TYPE,
	ubicacion: str,
) -> None:
	"""Notifica al admin una ubicación de delivery una sola vez por flujo."""

	if not ADMIN_TELEGRAM_ID:
		return

	if context.user_data.get("delivery_admin_notified"):
		return

	urow = obtener_usuario_por_telegram_id(update.effective_user.id)
	nombre_cli = nombre_publico_usuario(urow) if urow else ""
	if not nombre_cli:
		nombre_cli = update.effective_user.first_name or update.effective_user.username or "Usuario"
	texto_admin = (
		f"Nueva ubicación de delivery\n"
		f"Cliente: {nombre_cli} ({update.effective_user.id})\n"
		f"Ubicación: {_ubicacion_legible(ubicacion)}\n"
		"Responde con un monto para delivery en el chat del admin."
	)
	await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=texto_admin)

	geo = _parsear_geo(ubicacion)
	if geo is not None:
		await context.bot.send_location(
			chat_id=ADMIN_TELEGRAM_ID,
			latitude=geo[0],
			longitude=geo[1],
		)

	context.user_data["delivery_admin_notified"] = True


async def _continuar_pedido_tras_carrito(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Con tipo/ubicación/pago ya guardados en user_data, pide datos faltantes o muestra el resumen."""

	tipo_entrega = context.user_data.get("tipo_entrega")
	metodo_pago = context.user_data.get("metodo_pago")
	ubicacion_entrega = context.user_data.get("ubicacion_entrega")
	telegram_id = update.effective_user.id
	context.user_data["telegram_id"] = telegram_id

	msg = update.effective_message
	if msg is None:
		return ConversationHandler.END

	if not tipo_entrega:
		await msg.reply_text(
			"Indícame el tipo de entrega: delivery o pickup.",
			reply_markup=_markup_tipo_entrega(),
		)
		return PIDIENDO_TIPO_ENTREGA

	if tipo_entrega == "delivery":
		if not ubicacion_entrega:
			await msg.reply_text(
				"Perfecto, para delivery necesito tu ubicación. Puedes compartir ubicación de Telegram o escribir la dirección exacta."
			)
			return PIDIENDO_UBICACION

		context.user_data["ubicacion_entrega"] = ubicacion_entrega
		preparar_delivery_pendiente(telegram_id)
		await _notify_admin_delivery_location(update, context, ubicacion_entrega)

		costo_pendiente = obtener_costo_delivery_pendiente(telegram_id)
		if costo_pendiente is None:
			await msg.reply_text(
				"Ya tengo tu pedido y ubicación. Ahora el admin debe asignar el costo de delivery para continuar."
			)
			return ESPERANDO_COSTO_DELIVERY

		if metodo_pago not in {"efectivo", "pago movil"}:
			await msg.reply_text(
				"Indícame método de pago: efectivo o pago móvil.",
				reply_markup=_markup_metodo_pago(),
			)
			return PIDIENDO_METODO_PAGO

		context.user_data["metodo_pago"] = metodo_pago
		context.user_data["comprobante_file_id"] = context.user_data.get("comprobante_file_id")
		await msg.reply_text(await _build_resumen_pedido_async(context))
		return CONFIRMANDO_PEDIDO

	if not metodo_pago:
		metodo_pago = "presencial"
	context.user_data["metodo_pago"] = metodo_pago
	context.user_data["ubicacion_entrega"] = None
	context.user_data["comprobante_file_id"] = None
	await msg.reply_text(await _build_resumen_pedido_async(context))
	return CONFIRMANDO_PEDIDO


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
	"""Procesa texto libre y arranca/avanza pedido sin necesidad de comandos."""

	if update.effective_user is None:
		return ConversationHandler.END

	# Cotización rápida de delivery: el admin puede responder solo con un número aunque no esté en modo admin.
	if (
		ADMIN_TELEGRAM_ID is not None
		and update.effective_user.id == ADMIN_TELEGRAM_ID
		and obtener_delivery_pendiente() is not None
	):
		texto_monto = (update.message.text or "").strip()
		if texto_monto and re.fullmatch(r"\d+(?:[\.,]\d+)?", texto_monto.replace(",", ".")):
			pending_uid = obtener_delivery_pendiente()
			await admin_recibir_monto_delivery(update, context)
			# Mismo chat: no cerrar el flujo de pedido o los botones dejan de responder.
			if pending_uid is not None and pending_uid == update.effective_user.id:
				return PIDIENDO_METODO_PAGO
			return ConversationHandler.END

	if update.effective_user.id == ADMIN_TELEGRAM_ID and context.user_data.get("admin_mode"):
		texto_admin = (update.message.text or "").strip()
		try:
			if await _procesar_texto_admin(update, context, texto_admin):
				return ConversationHandler.END
		except Exception:
			await update.message.reply_text("No pude procesar esa instrucción. Escribe 'ayuda' para ver opciones o 'salir admin' para salir.")
			return ConversationHandler.END
		await update.message.reply_text(_admin_menu_text())
		return ConversationHandler.END

	texto = (update.message.text or "").strip()
	if not texto:
		return ConversationHandler.END

	tid = update.effective_user.id
	context.user_data["telegram_id"] = tid
	asegurar_usuario_telegram(tid, update.effective_user.username)

	if _texto_es_cancelacion(texto):
		return await cancelar_pedido(update, context)

	if _texto_editar_mis_datos(texto):
		return await iniciar_mis_datos(update, context)

	if not usuario_perfil_completo(tid):
		context.user_data.pop("registro_para_pedido", None)
		paso = siguiente_paso_registro_incompleto(tid)
		if paso == "cedula":
			await update.message.reply_text(
				"Para continuar falta tu **cédula** de identidad (solo números, 6 a 12 dígitos).",
				reply_markup=ReplyKeyboardRemove(),
			)
			return PIDIENDO_CEDULA_CLIENTE
		await update.message.reply_text(
			"Para comprar necesito tus datos. Escribe tu **nombre completo** "
			"(nombre y apellidos en una sola línea).",
			reply_markup=ReplyKeyboardRemove(),
		)
		return PIDIENDO_NOMBRE_CLIENTE

	if context.user_data.get("esperando_comprobante"):
		await update.message.reply_text(
			"El pedido ya fue confirmado. Para completarlo, envía la foto del comprobante.\n"
			"Si necesitas cambiar algo, cancela este pedido y haz uno nuevo."
		)
		return PIDIENDO_COMPROBANTE

	catalog = obtener_catalogo_disponible()
	disponibilidad = await _responder_disponibilidad_producto(update, context, texto, catalog)
	if disponibilidad is not None:
		return disponibilidad

	if _texto_fuera_de_contexto(texto):
		await update.message.reply_text(_texto_asesor_fuera_contexto())
		return _infer_estado_tras_volver_al_chat(context, tid) if _pedido_en_curso(context) else ConversationHandler.END

	if _texto_pregunta_identidad_helados_cali(texto):
		return await _responder_faq_identidad_helados_cali(update, context, tid)

	modo_faltaba_al_entrar = context.user_data.get("modo_precio") is None
	if modo_faltaba_al_entrar:
		modo_inferido = _modo_precio_desde_texto(texto)
		if modo_inferido in {"detal", "mayor"}:
			context.user_data["modo_precio"] = modo_inferido
		else:
			await _enviar_pregunta_modo_precio(update, context)
			return PIDIENDO_MODO_PRECIO

	pending_ambiguos_antes_slots = context.user_data.get("items_pendientes_clarificar", [])
	if pending_ambiguos_antes_slots:
		actual = pending_ambiguos_antes_slots[0]
		resolved_pending, remaining_item = _resolver_un_item_ambiguo(texto, actual, catalog)
		if resolved_pending:
			items_actuales = _items_desde_context(context)
			items_actuales.append(resolved_pending)
			try:
				await _guardar_items_en_context(update, context, items_actuales)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return PIDIENDO_PRODUCTO
			remaining_pending = pending_ambiguos_antes_slots[1:]
			if remaining_pending:
				context.user_data["items_pendientes_clarificar"] = remaining_pending
				await update.message.reply_text(
					_formatear_item_ambiguo(
						remaining_pending[0],
						len(pending_ambiguos_antes_slots) - len(remaining_pending) + 1,
						len(pending_ambiguos_antes_slots),
					)
				)
				return PIDIENDO_PRODUCTO
			context.user_data.pop("items_pendientes_clarificar", None)
			if _texto_quiere_cerrar_carrito(texto) or _texto_tiene_slots_post_carrito(texto):
				return await _continuar_pedido_tras_carrito(update, context)
			await update.message.reply_text(_texto_pregunta_mas_productos(context))
			return PIDIENDO_PRODUCTO

		context.user_data["items_pendientes_clarificar"] = pending_ambiguos_antes_slots
		await update.message.reply_text(
			_formatear_item_ambiguo(actual, 1, len(pending_ambiguos_antes_slots))
		)
		return PIDIENDO_PRODUCTO

	estado_gate = _infer_estado_tras_volver_al_chat(context, tid)
	t_entrega_gate, m_pago_gate = _extract_delivery_and_payment(texto)
	if (
		not modo_faltaba_al_entrar
		and estado_gate == PIDIENDO_PRODUCTO
		and not _texto_quiere_cerrar_carrito(texto)
		and not _texto_tiene_items_o_producto_pedido(texto, catalog)
		and (t_entrega_gate or m_pago_gate or _modo_precio_desde_texto(texto))
	):
		await update.message.reply_text(
			"Ahora estamos armando el carrito. Escribe productos y cantidades, o confirma cuando ya no quieras agregar más. "
			"Después te pediré entrega, ubicación y pago paso por paso."
		)
		return PIDIENDO_PRODUCTO

	if _infer_estado_tras_volver_al_chat(context, tid) == CONFIRMANDO_PEDIDO and (
		_texto_afirmativo(texto) or _texto_negativo(texto)
	):
		return await confirmar_pedido(update, context)

	chg = await _procesar_cambios_pre_confirmacion(update, context, texto, catalog, tid)
	if chg is not None:
		return chg
	if _texto_quiere_cerrar_carrito(texto):
		items_simple_cf = _items_desde_context(context)
		if not items_simple_cf:
			await update.message.reply_text("Tu pedido está vacío. Dime qué productos quieres.")
			return PIDIENDO_PRODUCTO
		modo_cf = context.user_data.get("modo_precio")
		cumple_cf, sub_cf, umb_cf = validar_minimo_compra_mayor(items_simple_cf, modo_cf)
		if not cumple_cf:
			await update.message.reply_text(
				f"En modo al mayor tu pedido debe sumar al menos ${float(umb_cf):.2f} USD a precio mayorista. "
				f"Llevas ${float(sub_cf):.2f}. Agrega más productos o cantidades."
			)
			return PIDIENDO_PRODUCTO
		try:
			context.user_data["items"] = items_simple_cf
			context.user_data["items_guardados"] = _preparar_items_guardados(items_simple_cf, modo_cf)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return PIDIENDO_PRODUCTO
		context.user_data.pop("producto", None)
		context.user_data.pop("cantidad", None)
		context.user_data.pop("stock_disponible", None)
		return await _continuar_pedido_tras_carrito(update, context)
	priorizado = await _priorizar_estado_esperado(update, context, texto)
	if priorizado is not None:
		return priorizado
	_guardar_slots_desde_texto(context, texto)

	if (
		_texto_editar_ubicacion(texto)
		and context.user_data.get("tipo_entrega") == "delivery"
		and context.user_data.get("ubicacion_entrega")
		and not context.user_data.get("esperando_comprobante")
	):
		_reset_cotizacion_delivery(context, tid)
		preparar_delivery_pendiente(tid)
		await update.message.reply_text(
			"Borré la ubicación y la cotización anterior. Envía la nueva dirección o comparte la ubicación en el mapa."
		)
		return PIDIENDO_UBICACION

	if _texto_pide_catalogo(texto):
		catalogo_quick = obtener_catalogo_disponible() or []
		m = context.user_data.get("modo_precio")
		if _resolver_ruta_catalogo_visual():
			await _enviar_catalogo(update, "Helados Cali — catálogo visual:", (catalogo_quick, m))
			await update.message.reply_text("Cuando quieras, seguimos con tu pedido.")
			return _infer_estado_tras_volver_al_chat(context, tid)
		if m and catalogo_quick:
			await _enviar_catalogo(update, "Catálogo:", (catalogo_quick, m))
			await update.message.reply_text("Cuando quieras, seguimos con tu pedido.")
			return _infer_estado_tras_volver_al_chat(context, tid)
		if not m:
			await update.message.reply_text(
				"Para mostrarte el catálogo con el precio correcto, primero elige **detal** o **mayor**.",
				reply_markup=_markup_modo_precio(),
			)
			return PIDIENDO_MODO_PRECIO
		await update.message.reply_text("En este momento no hay productos cargados en el catálogo.")
		return _infer_estado_tras_volver_al_chat(context, tid)

	estado_actual = _infer_estado_tras_volver_al_chat(context, tid)
	parsed_order = _parse_order_message(texto, catalog, estado_actual)
	if parsed_order.ubicacion_entrega and estado_actual == PIDIENDO_UBICACION and not parsed_order.has_items_or_pending:
		context.user_data["ubicacion_entrega"] = parsed_order.ubicacion_entrega
		return await _continuar_pedido_tras_carrito(update, context)

	estado_rem_post: int | None = None
	if parsed_order.remove_product_query:
		estado_rem_post = await _remover_item_del_carrito_por_fragmento(
			update, context, parsed_order.remove_product_query, catalog
		)

	t_lower_check = texto.strip().lower()
	if t_lower_check in {"editar", "modificar", "cambiar"}:
		if _items_desde_context(context):
			await update.message.reply_text(_texto_ayuda_editar_pedido(context))
			return PIDIENDO_PRODUCTO

	edicion = await _procesar_edicion_carrito(update, context, texto, catalog)
	if edicion is not None:
		return edicion

	quitar_match = re.match(r"(?i)^(quitar|eliminar|sacar|borrar)\s+(.+)$", texto.strip())
	if quitar_match:
		return await _remover_item_del_carrito_por_fragmento(
			update, context, quitar_match.group(2).strip(), catalog
		)

	if (
		estado_rem_post is not None
		and not parsed_order.items
		and not parsed_order.ambiguos
		and not parsed_order.unknown_terms
	):
		return estado_rem_post

	# Particionar instrucciones compuestas sin perder el texto original.
	instrucciones = split_instructions(texto)
	desconocidos = parsed_order.unknown_terms
	pending_ambiguos = context.user_data.get("items_pendientes_clarificar", [])
	if pending_ambiguos:
		actual = pending_ambiguos[0]
		resolved_pending, remaining_item = _resolver_un_item_ambiguo(texto, actual, catalog)
		if resolved_pending:
			items_actuales = _items_desde_context(context)
			items_actuales.append(resolved_pending)
			try:
				await _guardar_items_en_context(update, context, items_actuales)
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return PIDIENDO_PRODUCTO
			remaining_pending = pending_ambiguos[1:]
			if remaining_pending:
				context.user_data["items_pendientes_clarificar"] = remaining_pending
				await update.message.reply_text(
					_formatear_item_ambiguo(remaining_pending[0], len(pending_ambiguos) - len(remaining_pending) + 1, len(pending_ambiguos))
				)
				return PIDIENDO_PRODUCTO
			context.user_data.pop("items_pendientes_clarificar", None)
			if _texto_quiere_cerrar_carrito(texto) or _texto_tiene_slots_post_carrito(texto):
				return await _continuar_pedido_tras_carrito(update, context)
			await update.message.reply_text(_texto_pregunta_mas_productos(context))
			return PIDIENDO_PRODUCTO

		# No se pudo resolver el primero de la cola; pedir aclaración solo de ese producto.
		context.user_data["items_pendientes_clarificar"] = pending_ambiguos
		await update.message.reply_text(
			_formatear_item_ambiguo(actual, 1, len(pending_ambiguos))
		)
		return PIDIENDO_PRODUCTO

	# Detectar items múltiples desde el parser canónico, ejemplo: "2 Polet, 3 Conos y 1 Galón".
	found_items = parsed_order.items
	if found_items:
		try:
			await _sumar_items_al_carrito(update, context, found_items)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return PIDIENDO_PRODUCTO
		_aplicar_parse_slots(context, parsed_order)
		if estado_rem_post is not None:
			await update.message.reply_text(
				"También dejé registrados los productos nuevos en tu carrito (los datos del pedido siguen igual)."
			)

	pending_items = parsed_order.ambiguos
	if pending_items:
		context.user_data["items_pendientes_clarificar"] = pending_items
		if found_items:
			await update.message.reply_text(
				_formatear_item_ambiguo(pending_items[0], 1, len(pending_items))
			)
			return PIDIENDO_PRODUCTO
		# Si todo lo que se encontró es ambiguo, preguntar solo por el primero y mantener el resto en cola.
		await update.message.reply_text(_formatear_item_ambiguo(pending_items[0], 1, len(pending_items)))
		return PIDIENDO_PRODUCTO

	if found_items:
		if _texto_quiere_cerrar_carrito(texto) or _texto_tiene_slots_post_carrito(texto):
			return await _continuar_pedido_tras_carrito(update, context)
		await update.message.reply_text(_texto_pregunta_mas_productos(context))
		return PIDIENDO_PRODUCTO

	modelo_estado = await _procesar_interpretacion_modelo(update, context, texto, catalog, tid)
	if modelo_estado is not None:
		return modelo_estado

	recs_por_tags = recomendar_productos_por_consulta(texto, catalog)
	if not found_items and recs_por_tags:
		lineas_r = [f"- {p.get('nombre_producto', '')}" for p in recs_por_tags[:10]]
		await update.message.reply_text(
			"Estas opciones del catálogo podrían encajar con lo que buscas:\n"
			+ "\n".join(lineas_r)
			+ "\n\n"
			+ "Si quieres pedir alguna, escríbela con la cantidad."
		)
		return PIDIENDO_PRODUCTO

	intento_texto_actual = detect_intent(texto, catalog)
	entities_texto_actual = intento_texto_actual.get("entities", {})
	needs_product_clarification = False
	if _items_desde_context(context) and not found_items and not pending_items:
		per_item_unknowns = _build_pending_unknown_items_from_text(texto, catalog)[1]
		if per_item_unknowns:
			context.user_data["items_pendientes_clarificar"] = per_item_unknowns
			await update.message.reply_text(_formatear_item_ambiguo(per_item_unknowns[0], 1, len(per_item_unknowns)))
			return PIDIENDO_PRODUCTO
		if desconocidos:
			await update.message.reply_text(_mensaje_terminos_no_catalogo(desconocidos[:1]))
			return PIDIENDO_PRODUCTO
		if entities_texto_actual.get("product_clarify") and entities_texto_actual.get("product_candidates"):
			await update.message.reply_text(
				"No quedó claro cuál producto quieres agregar. Puede ser uno de estos:\n"
				+ "\n".join(f"- {c}" for c in entities_texto_actual["product_candidates"][:5])
				+ "\n\nPuedes responder con una sola palabra si eso lo aclara."
			)
			return PIDIENDO_PRODUCTO

	texto_norm_actual = normalize_text(texto)
	t_entrega_actual, m_pago_actual = _extract_delivery_and_payment(texto)
	loc_actual = _extract_location_from_text(texto)
	tiene_senal_directa_de_pedido = bool(
		found_items
		or pending_items
		or desconocidos
		or needs_product_clarification
		or entities_texto_actual.get("product")
		or entities_texto_actual.get("quantity") is not None
		or entities_texto_actual.get("product_clarify")
		or t_entrega_actual
		or m_pago_actual
		or loc_actual
		or any(cue in texto_norm_actual for cue in _ORDER_CUES)
		or bool(re.search(r"\d", texto_norm_actual))
	)

	# Slots acumulados: no se limpia contexto para permitir flujo flexible.
	order_detected = False
	items_detected = context.user_data.get("items", [])
	product_name = context.user_data.get("producto")
	quantity = context.user_data.get("cantidad")
	tipo_entrega = context.user_data.get("tipo_entrega")
	metodo_pago = context.user_data.get("metodo_pago")
	ubicacion_entrega = context.user_data.get("ubicacion_entrega")
	product_candidates: list[str] = []
	support_texts = []
	respuestas = []

	for instruccion in instrucciones:
		# Intent detection + extracción de entidades por instrucción.
		intento = detect_intent(instruccion, catalog)
		intent = intento.get("intent")
		entities = intento.get("entities", {})
		t_entrega, m_pago = _extract_delivery_and_payment(instruccion)
		loc = _extract_location_from_text(instruccion)

		if t_entrega and not tipo_entrega:
			tipo_entrega = t_entrega
		if m_pago and not metodo_pago:
			metodo_pago = m_pago
		if loc and not ubicacion_entrega:
			ubicacion_entrega = loc

		if intent == "order":
			order_detected = True
			if entities.get("product") and not product_name:
				product_name = entities.get("product")
			if entities.get("product_candidates"):
				product_candidates = entities.get("product_candidates", [])
			if entities.get("quantity") and not quantity:
				quantity = entities.get("quantity")
			if entities.get("product_clarify") and entities.get("product_candidates"):
				needs_product_clarification = True
				product_candidates = entities.get("product_candidates", [])
			if (
				not entities.get("product")
				and not (entities.get("product_clarify") and entities.get("product_candidates"))
			):
				uk_o = list_unknown_product_terms(instruccion, catalog)
				if uk_o:
					respuestas.append(_mensaje_terminos_no_catalogo(uk_o))
		elif intent == "support":
			if _looks_like_support_message(instruccion):
				support_texts.append(instruccion)
		elif intent != "support" and tiene_senal_negativa_es(instruccion) and _extract_support_reference(instruccion) is not None:
			# NLU a veces clasifica insultos/reclamos como greeting/order; si hay #pedido, enrutar a soporte.
			support_texts.append(instruccion)
		elif intent == "help":
			respuestas.append(
				generar_respuesta_natural(
					instruccion,
					contexto="el cliente pide ayuda en un chatbot de pedidos de helados",
					objetivo="explica brevemente cómo continuar o qué información falta",
				)
			)
		elif intent == "greeting":
			ins_n = normalize_text(instruccion)
			parece_pedido = any(cue in ins_n for cue in _ORDER_CUES) or bool(re.search(r"\d", ins_n))
			unk_ins = list_unknown_product_terms(instruccion, catalog)
			if (unk_ins or desconocidos) and context.user_data.get("modo_precio") is not None:
				u = list(dict.fromkeys([*unk_ins, *desconocidos]))
				respuestas.append(_mensaje_terminos_no_catalogo(u))
				order_detected = True
				continue
			if parece_pedido and (unk_ins or desconocidos):
				u = list(dict.fromkeys([*unk_ins, *desconocidos]))
				respuestas.append(_mensaje_terminos_no_catalogo(u))
				order_detected = True
				continue
			respuestas.append(
				generar_respuesta_natural(
					instruccion,
					contexto="el cliente saluda o conversa de forma casual con un chatbot de helados",
					objetivo="responde con un saludo corto y natural sin abrir un flujo nuevo",
				)
			)
		elif intent == "catalog":
			recs_catalog = recomendar_productos_por_consulta(instruccion, catalog)
			if recs_catalog and not _texto_pide_catalogo(instruccion):
				respuestas.append(
					"Sí, estas opciones del catálogo podrían encajar con lo que buscas:\n"
					+ "\n".join(f"- {p.get('nombre_producto', '')}" for p in recs_catalog[:10])
					+ "\n\nSi quieres pedir alguna, escríbela con la cantidad."
				)
			else:
				respuestas.append("__CATALOGO__")
		elif intent == "price":
			if entities.get("product"):
				producto_db = obtener_producto_disponible_por_nombre(entities["product"])
				if producto_db is not None:
					respuestas.append(_formatear_precio_producto(producto_db, context.user_data.get("modo_precio")))
				else:
					ukp = list_unknown_product_terms(instruccion, catalog)
					if ukp:
						respuestas.append(_mensaje_terminos_no_catalogo(ukp))
					else:
						respuestas.append(
							"No encontré ese nombre en el catálogo.\n" + _texto_recordatorio_nombre_catalogo()
						)
			else:
				respuestas.append("__CATALOGO__")
		elif entities.get("product_clarify") and entities.get("product_candidates"):
			ukc = list_unknown_product_terms(instruccion, catalog)
			partes = []
			if ukc:
				partes.append(_mensaje_terminos_no_catalogo(ukc))
			partes.append(
				"No quedó claro cuál de estas líneas del catálogo es:\n"
				+ "\n".join(f"- {c}" for c in entities["product_candidates"][:5])
			)
			if not ukc:
				partes.append(_texto_recordatorio_nombre_catalogo())
			respuestas.append("\n\n".join(partes))
			needs_product_clarification = True
		elif intent == "cancel":
			if tiene_senal_directa_de_pedido or parsed_order.has_items_or_pending or parsed_order.has_slots:
				order_detected = True
				continue
			context.user_data.clear()
			await update.message.reply_text("Cancelé el flujo actual. Si quieres, puedes escribir tu pedido de nuevo con lenguaje natural.")
			return ConversationHandler.END
		elif intent == "status":
			respuestas.append(
				generar_respuesta_natural(
					instruccion,
					contexto="el cliente quiere saber el estado de un pedido en un chatbot de helados",
					objetivo="pide el número de pedido o explica que lo necesitas para revisar el estado",
				)
			)
		else:
			respuestas.append(
				generar_respuesta_natural(
					instruccion,
					contexto="el mensaje no tiene una intención clara en un chatbot de helados",
					objetivo="redirige al cliente al pedido, soporte o catálogo con una sola frase",
				)
			)

	if order_detected or tiene_senal_directa_de_pedido:
		context.user_data["telegram_id"] = update.effective_user.id

		if items_detected:
			try:
				context.user_data["items_guardados"] = _preparar_items_guardados(items_detected, context.user_data.get("modo_precio"))
			except ValueError as exc:
				await update.message.reply_text(str(exc))
				return PIDIENDO_PRODUCTO

		if not product_name and not items_detected:
			if needs_product_clarification and product_candidates:
				extra = ""
				if desconocidos:
					extra = _mensaje_terminos_no_catalogo(desconocidos) + "\n\n"
				await update.message.reply_text(
					extra
					+ "No identifiqué un producto exacto. Puedes elegir uno de estos:\n"
					+ "\n".join(f"- {c}" for c in product_candidates[:8])
					+ ("\n\n" + _texto_recordatorio_nombre_catalogo() if not desconocidos else "")
				)
				return PIDIENDO_PRODUCTO

		if not product_name and not items_detected and catalog:
			modo_cat = context.user_data.get("modo_precio") or "detal"
			await _enviar_catalogo(
				update,
				"Entendí que quieres pedir. Productos disponibles:",
				(catalog, modo_cat),
			)
			tail = "Escribe el nombre del producto tal como aparece en la lista."
			if desconocidos:
				tail = _mensaje_terminos_no_catalogo(desconocidos) + "\n\n" + tail
			else:
				tail = _texto_recordatorio_nombre_catalogo() + "\n\n" + tail
			await update.message.reply_text(tail)
			return PIDIENDO_PRODUCTO
		elif not product_name and not items_detected:
			await update.message.reply_text("En este momento no hay productos disponibles.")
			return ConversationHandler.END

		if items_detected:
			context.user_data["stock_disponible"] = min(item["cantidad"] for item in context.user_data["items_guardados"])
		elif product_name:
			producto_db = obtener_producto_disponible_por_nombre(product_name)
			if producto_db is None:
				ukf = list_unknown_product_terms(texto, catalog)
				msg_nf = f"No encontré «{product_name}» en el catálogo con stock."
				if ukf:
					msg_nf = _mensaje_terminos_no_catalogo(ukf) + "\n\n" + msg_nf
				else:
					msg_nf += "\n" + _texto_recordatorio_nombre_catalogo()
				await update.message.reply_text(msg_nf)
				return PIDIENDO_PRODUCTO

			context.user_data["producto"] = producto_db["nombre_producto"]
			context.user_data["stock_disponible"] = producto_db["cantidad"]

			if quantity is None:
				intento_mq = detect_intent(texto.strip(), catalog)
				ent_mq = intento_mq.get("entities") or {}
				prefix_mq = ""
				if (
					intento_mq.get("intent") == "order"
					and ent_mq.get("product")
					and normalize_text(str(ent_mq.get("product")))
					== normalize_text(str(producto_db["nombre_producto"]))
					and "quantity" in (ent_mq.get("missing_fields") or [])
				):
					prefix_mq = (
						f"Me falta la **cantidad** para «{producto_db['nombre_producto']}» "
						f"(por ejemplo: 4 {producto_db['nombre_producto']}).\n\n"
					)
				await update.message.reply_text(
					prefix_mq
					+ generar_respuesta_natural(
						texto,
						contexto="flujo de pedido en curso y falta la cantidad",
						objetivo=f"confirma producto {producto_db['nombre_producto']} y pide cantidad en una sola frase",
					)
				)
				return PIDIENDO_CANTIDAD

			if quantity <= 0 or quantity > int(producto_db["cantidad"]):
				await update.message.reply_text(
					"Esa cantidad no está disponible. Prueba con un número menor. ¿Cuántas unidades deseas?"
				)
				return PIDIENDO_CANTIDAD

			context.user_data["cantidad"] = int(quantity)

		items_simple_mv = _items_desde_context(context)
		if items_simple_mv:
			cumple_mv, sub_mv, umb_mv = validar_minimo_compra_mayor(
				items_simple_mv, context.user_data.get("modo_precio")
			)
			if not cumple_mv:
				await update.message.reply_text(
					f"En modo al mayor tu pedido debe sumar al menos ${float(umb_mv):.2f} USD a precio mayorista. "
					f"Llevas ${float(sub_mv):.2f}. Agrega más productos o cantidades."
				)
				return PIDIENDO_PRODUCTO

		if tipo_entrega:
			context.user_data["tipo_entrega"] = tipo_entrega
		if ubicacion_entrega:
			context.user_data["ubicacion_entrega"] = ubicacion_entrega
		if metodo_pago:
			context.user_data["metodo_pago"] = metodo_pago
		_aplicar_parse_slots(context, parsed_order)
		return await _continuar_pedido_tras_carrito(update, context)

	if support_texts:
		support_join = " ".join(support_texts).strip()
		pedido_id = _extract_support_reference(support_join)
		if pedido_id is None:
			await update.message.reply_text(
				"Claro, para ayudarte necesito el número de pedido. Envíamelo y te ayudo enseguida."
			)
			return ConversationHandler.END

		mensaje_soporte = re.sub(r"(?i)soporte", "", support_join).strip()
		mensaje_soporte = re.sub(r"(?i)pedido\s*#?\s*\d+", "", mensaje_soporte).strip()
		if not mensaje_soporte:
			await update.message.reply_text("Te faltó el mensaje de soporte. Ejemplo: 'soporte pedido #x sigo esperando'.")
			return ConversationHandler.END

		polaridad = analizar_sentimiento(mensaje_soporte)
		if polaridad < -0.05:
			await update.message.reply_text(
				"He detectado que tienes un problema, he marcado tu caso como prioritario para un asesor humano"
			)

		try:
			pedido = registrar_mensaje_cliente(
				pedido_id, update.effective_user.id, mensaje_soporte, polaridad=polaridad
			)
		except ValueError as exc:
			await update.message.reply_text(str(exc))
			return ConversationHandler.END

		await update.message.reply_text("Tu mensaje de soporte fue enviado al admin.")
		if ADMIN_TELEGRAM_ID:
			await context.bot.send_message(
				chat_id=ADMIN_TELEGRAM_ID,
				text=(
					f"Mensaje cliente en pedido #{pedido_id}\n"
					f"{_linea_cliente_pedido(pedido)}\n"
					f"Texto: {mensaje_soporte}\n\n"
					f"Polaridad: {polaridad}\n"
					f"Responder: /admin_responder {pedido_id} <mensaje>\n"
					f"Ver chat: /admin_chat {pedido_id}"
				),
			)
		return ConversationHandler.END

	if respuestas:
		if "__CATALOGO__" in respuestas:
			modo_cat = context.user_data.get("modo_precio") or "detal"
			await _enviar_catalogo(update, "Catálogo:", (catalog, modo_cat))
			respuestas = [r for r in respuestas if r != "__CATALOGO__"]
		if respuestas:
			await update.message.reply_text("\n".join(respuestas))
		if needs_product_clarification:
			return PIDIENDO_PRODUCTO
		return _infer_estado_tras_volver_al_chat(context, tid)
	else:
		if desconocidos and context.user_data.get("modo_precio") is not None:
			await update.message.reply_text(_mensaje_terminos_no_catalogo(desconocidos))
			return PIDIENDO_PRODUCTO
		await update.message.reply_text(_texto_asesor_fuera_contexto())
		if _pedido_en_curso(context):
			return _infer_estado_tras_volver_al_chat(context, tid)
	return ConversationHandler.END


async def admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Muestra historial de conversación de un pedido."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	try:
		pedido_id = _parse_pedido_id(context)
		pedido, chat = obtener_chat_admin(pedido_id, limite=30)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	if not chat:
		await update.message.reply_text(
			f"Pedido #{pedido_id} sin mensajes de chat registrados."
		)
		return

	lineas = [
		f"[{m['fecha_creacion']}] {m['emisor']}: {m['mensaje']}"
		for m in chat
	]
	encabezado = (
		f"Chat del pedido #{pedido_id}\n"
		f"{_linea_cliente_pedido(pedido)}\n"
	)
	await update.message.reply_text(encabezado + "\n" + "\n".join(lineas[-20:]))


async def admin_responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Envía respuesta del admin al cliente y la guarda en chat del pedido."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	if len(context.args) < 2:
		await update.message.reply_text(
			"Uso: /admin_responder <id_pedido> <mensaje>"
		)
		return

	try:
		pedido_id = int(context.args[0])
	except ValueError:
		await update.message.reply_text("El id del pedido debe ser numérico.")
		return

	mensaje = " ".join(context.args[1:]).strip()
	if not mensaje:
		await update.message.reply_text("Debes escribir un mensaje para el cliente.")
		return

	try:
		pedido = registrar_mensaje_admin(pedido_id, mensaje)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await context.bot.send_message(
		chat_id=pedido["telegram_id"],
		text=f"Mensaje de soporte sobre tu pedido #{pedido_id}: {mensaje}",
	)
	await update.message.reply_text("Mensaje enviado al cliente.")


async def admin_ver_pago_movil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Muestra los datos de pago móvil actuales."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	datos = obtener_datos_pago_movil()
	await update.message.reply_text(
		"Datos de pago móvil actuales:\n"
		f"- Teléfono: {datos['telefono']}\n"
		f"- Cédula: {datos['cedula']}\n"
		f"- Banco: {datos['banco']}"
	)


async def admin_set_pago_movil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Actualiza datos de pago móvil desde comando admin."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	if len(context.args) < 3:
		await update.message.reply_text(
			"Uso: /admin_set_pago_movil <telefono> <cedula> <banco>"
		)
		return

	telefono = context.args[0].strip()
	cedula = context.args[1].strip()
	banco = " ".join(context.args[2:]).strip()

	actualizar_datos_pago_movil(telefono, cedula, banco)
	await update.message.reply_text("Datos de pago móvil actualizados correctamente.")


async def admin_productos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Lista catálogo de productos para administración."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	productos = obtener_catalogo_admin()
	if not productos:
		await update.message.reply_text("No hay productos cargados.")
		return

	lineas: list[str] = []
	for p in productos:
		base = (
			f"- {p['nombre_producto']} | ${p.get('precio_detal', 0):.2f} "
			f"(mayor ${p.get('precio_mayor', p.get('precio_detal', 0)):.2f}) | stock: {p['cantidad']}"
		)
		desc = (p.get("descripcion") or "").strip()
		if desc:
			snippet = desc[:140] + ("…" if len(desc) > 140 else "")
			base += f"\n  {snippet}"
		base += etiquetas_resumen_linea(p.get("etiquetas") or [])
		lineas.append(base)
	await update.message.reply_text("Catálogo:\n" + "\n".join(lineas[:50]))


async def admin_add_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Crea un producto nuevo desde Telegram."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	if len(context.args) < 4:
		await update.message.reply_text(
			"Uso: /admin_add_producto <nombre> | <precio_detal_usd> | <precio_mayor_usd> | <stock>"
		)
		return

	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|")]
	if len(partes) != 4:
		await update.message.reply_text(
			"Formato inválido. Ejemplo: /admin_add_producto Nombre | <precio_detal> | <precio_mayor> | <stock>"
		)
		return

	nombre, precio_detal_raw, precio_mayor_raw, stock_raw = partes
	try:
		precio_detal = float(precio_detal_raw)
		precio_mayor = float(precio_mayor_raw)
		stock = int(stock_raw)
		producto_id = admin_crear_producto(nombre, precio_detal, precio_mayor, stock)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text(f"Producto creado. ID: {producto_id}")


async def admin_set_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Actualiza precio de producto desde Telegram."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|")]
	if len(partes) != 2:
		await update.message.reply_text(
			"Uso: /admin_set_precio <nombre> | <nuevo_precio_usd>"
		)
		return

	nombre, precio_raw = partes
	try:
		nuevo_precio = float(precio_raw)
		# Compat: actualizar solo precio detal
		admin_actualizar_precios(nombre, nuevo_precio, None)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text("Precio detal actualizado correctamente.")


async def admin_set_precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Actualiza ambos precios (detal y mayor) de un producto desde Telegram."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|")]
	if len(partes) != 3:
		await update.message.reply_text(
			"Uso: /admin_set_precios <nombre> | <nuevo_precio_detal_usd> | <nuevo_precio_mayor_usd>"
		)
		return

	nombre, precio_detal_raw, precio_mayor_raw = partes
	try:
		nuevo_precio_detal = float(precio_detal_raw)
		nuevo_precio_mayor = float(precio_mayor_raw)
		admin_actualizar_precios(nombre, nuevo_precio_detal, nuevo_precio_mayor)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text("Precios actualizados correctamente.")


async def admin_set_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Actualiza stock de producto desde Telegram."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|")]
	if len(partes) != 2:
		await update.message.reply_text(
			"Uso: /admin_set_stock <nombre> | <nuevo_stock>"
		)
		return

	nombre, stock_raw = partes
	try:
		nuevo_stock = int(stock_raw)
		admin_actualizar_stock(nombre, nuevo_stock)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text("Stock actualizado correctamente.")


async def admin_del_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Elimina un producto por nombre desde Telegram."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return

	nombre = " ".join(context.args).strip()
	if not nombre:
		await update.message.reply_text(
			"Uso: /admin_del_producto <nombre>"
		)
		return

	try:
		admin_eliminar_producto(nombre)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return

	await update.message.reply_text("Producto eliminado correctamente.")


async def admin_set_descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Asigna descripción a un producto: nombre | texto."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|", 1)]
	if len(partes) != 2 or not partes[0]:
		await update.message.reply_text("Uso: /admin_set_descripcion <nombre producto> | <descripción>")
		return
	nombre, desc = partes
	try:
		admin_set_descripcion_producto(nombre, desc)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return
	await update.message.reply_text("Descripción guardada.")


async def admin_set_etiquetas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Reemplaza todas las etiquetas: nombre | tag1, tag2, tag3."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|", 1)]
	if len(partes) != 2 or not partes[0]:
		await update.message.reply_text("Uso: /admin_set_etiquetas <nombre> | tag1, tag2, tag3")
		return
	nombre, tags_raw = partes
	tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
	try:
		admin_set_etiquetas_producto(nombre, tags)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return
	await update.message.reply_text(f"Etiquetas actualizadas ({len(tags)}): {', '.join(tags) if tags else '(ninguna)'}")


async def admin_etiqueta_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Añade una etiqueta: nombre producto | etiqueta."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|", 1)]
	if len(partes) != 2 or not partes[0] or not partes[1]:
		await update.message.reply_text("Uso: /admin_etiqueta_add <nombre producto> | <etiqueta>")
		return
	nombre, tag = partes
	try:
		admin_etiqueta_anadir(nombre, tag)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return
	await update.message.reply_text(f"Etiqueta «{tag}» asociada a «{nombre}».")


async def admin_etiqueta_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Quita una etiqueta: nombre producto | etiqueta."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	texto = " ".join(context.args)
	partes = [p.strip() for p in texto.split("|", 1)]
	if len(partes) != 2 or not partes[0] or not partes[1]:
		await update.message.reply_text("Uso: /admin_etiqueta_del <nombre producto> | <etiqueta>")
		return
	nombre, tag = partes
	try:
		admin_etiqueta_quitar(nombre, tag)
	except ValueError as exc:
		await update.message.reply_text(str(exc))
		return
	await update.message.reply_text(f"Etiqueta «{tag}» quitada de «{nombre}».")


async def admin_etiquetas_ver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Muestra descripción y etiquetas de un producto."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	nombre = " ".join(context.args).strip()
	if not nombre:
		await update.message.reply_text("Uso: /admin_etiquetas_ver <nombre del producto>")
		return
	from ..data.repositories.productos_repo import obtener_producto_por_nombre

	p = obtener_producto_por_nombre(nombre)
	if not p:
		await update.message.reply_text("No encontré ese producto.")
		return
	tags = p.get("etiquetas") or []
	desc = (p.get("descripcion") or "").strip() or "(sin descripción)"
	tags_txt = ", ".join(tags) if tags else "(ninguna)"
	await update.message.reply_text(
		f"{p['nombre_producto']}\nDescripción: {desc}\nEtiquetas: {tags_txt}",
	)


async def admin_inferir_etiquetas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Rellena etiquetas inferidas solo donde la lista está vacía."""

	if not _check_admin(update):
		await update.message.reply_text("No autorizado.")
		return
	try:
		n = admin_inferir_etiquetas_todos()
	except Exception as exc:
		await update.message.reply_text(f"No se pudo completar: {exc}")
		return
	await update.message.reply_text(f"Listo. Productos actualizados con inferencia: {n}.")
