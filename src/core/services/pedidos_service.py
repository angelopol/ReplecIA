"""Lógica de negocio para pedidos."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from decimal import Decimal, ROUND_HALF_UP

from bcv_exchange import get_exchange_rate

from ...data.repositories.pedidos_repo import (
	actualizar_costo_delivery,
	actualizar_estado_pedido,
	insertar_pedido,
	listar_chat_pedido,
	listar_pedidos_admin,
	marcar_stock_descontado,
	obtener_items_pedido,
	obtener_pedido_por_id,
	registrar_mensaje_pedido,
)
from ...data.repositories.config_repo import delete_config, get_config, set_config

_log = logging.getLogger(__name__)

DELIVERY_PENDING_USER_KEY = "delivery.pending_user_id"
DELIVERY_QUOTE_PREFIX = "delivery.quote."


def _delivery_quote_key(telegram_id: int) -> str:
	"""Construye la clave de configuración para el monto de delivery pendiente."""

	return f"{DELIVERY_QUOTE_PREFIX}{telegram_id}"


def preparar_delivery_pendiente(telegram_id: int) -> None:
	"""Marca un delivery como pendiente de monto por parte del admin."""

	set_config(DELIVERY_PENDING_USER_KEY, str(telegram_id))


def obtener_delivery_pendiente() -> int | None:
	"""Devuelve el telegram_id del delivery pendiente más reciente."""

	pending = get_config(DELIVERY_PENDING_USER_KEY)
	if not pending:
		return None
	try:
		return int(pending)
	except ValueError:
		return None


def guardar_costo_delivery_pendiente(telegram_id: int, delivery_costo_usd: float) -> None:
	"""Guarda el costo de delivery para el pedido pendiente de un usuario."""

	set_config(_delivery_quote_key(telegram_id), str(delivery_costo_usd))
	delete_config(DELIVERY_PENDING_USER_KEY)


def obtener_costo_delivery_pendiente(telegram_id: int) -> Decimal | None:
	"""Lee el costo de delivery pendiente para un usuario, si ya fue definido por admin."""

	valor = get_config(_delivery_quote_key(telegram_id))
	if not valor:
		return None
	try:
		return _normalizar_numero_bcv(valor)
	except Exception:
		return None


def limpiar_costo_delivery_pendiente(telegram_id: int) -> None:
	"""Elimina el costo de delivery pendiente de un usuario."""

	delete_config(_delivery_quote_key(telegram_id))


from ...core.product_tags import parse_etiquetas_list, significant_tokens_busqueda
from ...data.repositories.productos_repo import (
	actualizar_descripcion,
	anadir_etiqueta,
	actualizar_precios_producto,
	actualizar_stock_producto,
	descontar_stock,
	eliminar_producto_por_nombre,
	establecer_etiquetas,
	insertar_producto,
	listar_productos,
	listar_productos_disponibles,
	obtener_producto_por_nombre,
	quitar_etiqueta,
)
from ...ia.nlu import apply_colloquial_helado_terms, normalize_text, strip_control_commands_for_product_search
from ...data.repositories.usuarios_repo import (
	crear_usuario,
	obtener_usuario_por_telegram_id,
)
from ...config.settings import (
	PAGO_MOVIL_BANCO,
	PAGO_MOVIL_CEDULA,
	PAGO_MOVIL_TELEFONO,
	PRECIO_MAYOR_UMBRAL,
)




def _normalizar_numero_bcv(valor: str) -> Decimal:
	"""Normaliza un número del BCV y lo convierte a Decimal."""

	v = valor.strip()
	if "," in v and "." in v:
		if v.rfind(",") > v.rfind("."):
			v = v.replace(".", "").replace(",", ".")
		else:
			v = v.replace(",", "")
	elif "," in v:
		v = v.replace(".", "").replace(",", ".")

	return Decimal(v)


def _leer_tasa_bcv_desde_cache() -> tuple[Decimal, str | None, str] | None:
	"""Devuelve (tasa, fecha, 'cache') si hay una tasa persistida; si no, None."""

	tasa_cache = get_config("bcv.usd_tasa")
	if not tasa_cache:
		return None
	try:
		tasa = _normalizar_numero_bcv(tasa_cache)
		fecha = get_config("bcv.fecha_valor")
		return tasa, (fecha or None), "cache"
	except Exception:
		return None


def obtener_tasa_usd_bcv(*, timeout_s: float = 8.0) -> tuple[Decimal, str | None, str]:
	"""Obtiene la tasa USD del BCV usando bcv_exchange con fallback cacheado.

	Devuelve (tasa, fecha_valor, origen) donde `origen` es:
	- "live": lectura en vivo exitosa
	- "cache": lectura desde cache local (por timeout/error de red)
	"""

	last_error: Exception | None = None
	try:
		# bcv_exchange puede colgarse si el sitio del BCV está lento; evitamos bloquear el bot indefinidamente.
		with ThreadPoolExecutor(max_workers=1) as executor:
			future = executor.submit(get_exchange_rate)
			result = future.result(timeout=float(timeout_s))
		exchange_rates = result.get("exchange_rates", {})
		usd_value = exchange_rates.get("USD")
		if usd_value is None:
			raise ValueError("bcv_exchange no devolvió tasa USD.")

		tasa = _normalizar_numero_bcv(str(usd_value))
		fecha_raw = result.get("date_of_change")
		fecha_valor = str(fecha_raw) if fecha_raw is not None else None

		set_config("bcv.usd_tasa", str(tasa))
		set_config("bcv.fecha_valor", fecha_valor or "")
		return tasa, fecha_valor, "live"
	except FuturesTimeoutError as exc:
		last_error = exc
		_log.warning("Timeout leyendo tasa BCV; usando cache si existe.")
	except Exception as exc:
		last_error = exc

	# Fallback: usar última tasa cacheada si BCV no está disponible temporalmente.
	cached = _leer_tasa_bcv_desde_cache()
	if cached is not None:
		return cached

	if last_error:
		raise ValueError("No se pudo leer la tasa USD del BCV.") from last_error
	raise ValueError("No se pudo leer la tasa USD del BCV.")


def prefetch_bcv_tasa_en_background() -> None:
	"""Intenta precargar la tasa BCV sin bloquear el arranque del bot."""

	def _run() -> None:
		try:
			obtener_tasa_usd_bcv()
		except Exception as exc:
			_log.warning("No se pudo precargar tasa BCV al iniciar: %s", exc)

	threading.Thread(target=_run, name="bcv-prefetch", daemon=True).start()


def obtener_umbral_precio_mayor_usd() -> Decimal:
	"""Monto mínimo del pedido (subtotal a precio al mayor) para comprar en modo mayor."""

	try:
		umbral_cfg = get_config("precio_mayor.umbral") or PRECIO_MAYOR_UMBRAL
		return Decimal(str(umbral_cfg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	except Exception:
		return Decimal("10.00")


def subtotal_carrito_a_precio_mayor_usd(items_simple: list[dict]) -> Decimal:
	"""Suma (precio_mayor × cantidad) por línea. `items_simple`: [{'producto': nombre, 'cantidad': int}, ...]."""

	total = Decimal("0")
	for item in items_simple:
		nombre = str(item.get("producto", "")).strip()
		cantidad = int(item.get("cantidad", 0))
		if not nombre or cantidad <= 0:
			continue
		db = obtener_producto_por_nombre(nombre)
		if db is None:
			continue
		precio_mayor = Decimal(str(db.get("precio_mayor", db.get("precio_detal", 0))))
		total += precio_mayor * Decimal(cantidad)
	return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validar_minimo_compra_mayor(items_simple: list[dict], modo_precio: str | None) -> tuple[bool, Decimal, Decimal]:
	"""Si modo es mayor, exige subtotal a precio mayor >= umbral admin. Devuelve (cumple, subtotal_mayor, umbral)."""

	umbral = obtener_umbral_precio_mayor_usd()
	if modo_precio != "mayor":
		return True, Decimal("0"), umbral
	sub = subtotal_carrito_a_precio_mayor_usd(items_simple)
	return sub >= umbral, sub, umbral


def obtener_resumen_montos(
	producto: str,
	cantidad: int,
	delivery_costo_usd: float = 0,
	modo_precio: str | None = None,
	*,
	incluir_bcv: bool = True,
) -> dict:
	"""Calcula subtotal/total en USD y, si aplica, equivalente en Bs según BCV.

	El modo `modo_precio` ('detal' o 'mayor') lo elige el cliente al inicio; si es None se asume 'detal'.
	El mínimo para pedidos al mayor se valida sobre el carrito completo (`validar_minimo_compra_mayor`).
	"""

	producto_db = obtener_producto_por_nombre(producto)
	if producto_db is None:
		raise ValueError("El producto no existe en el catálogo.")

	precio_detal = Decimal(str(producto_db.get("precio_detal", 0)))
	precio_mayor = Decimal(str(producto_db.get("precio_mayor", precio_detal)))

	if modo_precio == "mayor":
		precio_usd = precio_mayor
	else:
		precio_usd = precio_detal

	subtotal_usd = (precio_usd * Decimal(cantidad)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	delivery_usd = Decimal(str(delivery_costo_usd)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	total_usd = (subtotal_usd + delivery_usd).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

	tasa_bcv = None
	fecha_valor = None
	total_bs = None
	bcv_origen: str | None = None

	if incluir_bcv:
		try:
			tasa_bcv, fecha_valor, bcv_origen = obtener_tasa_usd_bcv()
			total_bs = (total_usd * tasa_bcv).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
		except Exception:
			# Permitir que el resumen muestre USD aunque BCV no esté disponible temporalmente.
			pass

	return {
		"precio_usd": precio_usd,
		"subtotal_usd": subtotal_usd,
		"delivery_costo_usd": delivery_usd,
		"total_usd": total_usd,
		"tasa_bcv": tasa_bcv,
		"fecha_valor": fecha_valor,
		"total_bs": total_bs,
		"bcv_origen": bcv_origen,
	}


def _normalizar_items(items: list[dict]) -> list[dict]:
	"""Agrupa items repetidos y valida cantidades positivas."""

	agregados: dict[int, dict] = {}
	for item in items:
		producto_id = int(item["producto_id"])
		cantidad = int(item.get("cantidad", 0))
		precio_unitario = Decimal(str(item.get("precio_unitario", 0)))
		if cantidad <= 0:
			raise ValueError("La cantidad de cada producto debe ser mayor que cero.")
		if producto_id not in agregados:
			agregados[producto_id] = {
				"producto_id": producto_id,
				"cantidad": 0,
				"precio_unitario": precio_unitario,
			}
		agregados[producto_id]["cantidad"] += cantidad
		if precio_unitario > 0:
			agregados[producto_id]["precio_unitario"] = precio_unitario

	return list(agregados.values())


def obtener_catalogo_disponible() -> list[dict]:
	"""Devuelve el catálogo de productos con stock para el flujo de pedidos."""

	return listar_productos_disponibles()


def obtener_datos_pago_movil() -> dict:
	"""Obtiene datos de pago móvil desde DB con fallback a settings."""

	telefono = get_config("pago_movil.telefono") or PAGO_MOVIL_TELEFONO
	cedula = get_config("pago_movil.cedula") or PAGO_MOVIL_CEDULA
	banco = get_config("pago_movil.banco") or PAGO_MOVIL_BANCO
	return {
		"telefono": telefono,
		"cedula": cedula,
		"banco": banco,
	}


def actualizar_datos_pago_movil(telefono: str, cedula: str, banco: str) -> None:
	"""Actualiza datos de pago móvil para mostrarlos en el resumen."""

	set_config("pago_movil.telefono", telefono)
	set_config("pago_movil.cedula", cedula)
	set_config("pago_movil.banco", banco)


def obtener_catalogo_admin() -> list[dict]:
	"""Devuelve catálogo completo para administración."""

	return listar_productos()


def admin_crear_producto(nombre_producto: str, precio_detal: float, precio_mayor: float, cantidad: int) -> int:
	"""Crea un producto nuevo en catálogo con precios detal y mayor."""

	if precio_detal <= 0 or precio_mayor < 0:
		raise ValueError("Los precios deben ser números válidos y mayores o iguales a 0.")
	if cantidad < 0:
		raise ValueError("La cantidad no puede ser negativa.")

	try:
		return insertar_producto(nombre_producto, precio_detal, precio_mayor, cantidad)
	except Exception as exc:
		raise ValueError("No se pudo crear el producto (puede que ya exista).") from exc


def admin_actualizar_precios(nombre_producto: str, nuevo_precio_detal: float | None, nuevo_precio_mayor: float | None) -> None:
	"""Actualiza precios de un producto existente."""

	if nuevo_precio_detal is not None and nuevo_precio_detal <= 0:
		raise ValueError("El precio detal debe ser mayor a 0.")
	if nuevo_precio_mayor is not None and nuevo_precio_mayor < 0:
		raise ValueError("El precio mayor no puede ser negativo.")

	if not actualizar_precios_producto(nombre_producto, nuevo_precio_detal, nuevo_precio_mayor):
		raise ValueError("No existe un producto con ese nombre.")


def admin_actualizar_stock(nombre_producto: str, nueva_cantidad: int) -> None:
	"""Actualiza stock de un producto existente."""

	if nueva_cantidad < 0:
		raise ValueError("La cantidad no puede ser negativa.")

	if not actualizar_stock_producto(nombre_producto, nueva_cantidad):
		raise ValueError("No existe un producto con ese nombre.")


def admin_eliminar_producto(nombre_producto: str) -> None:
	"""Elimina un producto del catálogo."""

	if not eliminar_producto_por_nombre(nombre_producto):
		raise ValueError("No existe un producto con ese nombre.")


def admin_set_descripcion_producto(nombre_producto: str, descripcion: str) -> None:
	if not actualizar_descripcion(nombre_producto, descripcion):
		raise ValueError("No existe un producto con ese nombre.")


def admin_set_etiquetas_producto(nombre_producto: str, etiquetas: list[str]) -> None:
	if not establecer_etiquetas(nombre_producto, etiquetas):
		raise ValueError("No existe un producto con ese nombre.")


def admin_etiqueta_anadir(nombre_producto: str, etiqueta: str) -> None:
	if not anadir_etiqueta(nombre_producto, etiqueta):
		raise ValueError("No existe un producto con ese nombre.")


def admin_etiqueta_quitar(nombre_producto: str, etiqueta: str) -> None:
	if not quitar_etiqueta(nombre_producto, etiqueta):
		raise ValueError("No existe un producto con ese nombre.")


def admin_inferir_etiquetas_todos() -> int:
	"""Vuelve a inferir etiquetas solo donde la lista quedó vacía."""

	from ...core.product_tags import infer_etiquetas_desde_nombre, serialize_etiquetas

	n = 0
	for p in listar_productos():
		tags = p.get("etiquetas") or []
		if tags:
			continue
		nombre = p.get("nombre_producto", "")
		new_tags = infer_etiquetas_desde_nombre(nombre)
		if not new_tags:
			continue
		if establecer_etiquetas(nombre, new_tags):
			n += 1
	return n


def recomendar_productos_por_consulta(texto: str, catalog: list[dict]) -> list[dict]:
	"""Ordena productos por coincidencia de tokens con etiquetas y nombre (sin sustituir al matching exacto)."""

	tn = apply_colloquial_helado_terms(normalize_text(strip_control_commands_for_product_search(texto)))
	toks = significant_tokens_busqueda(tn)
	if not toks:
		return []
	scored: list[tuple[int, dict]] = []
	for p in catalog:
		if int(p.get("cantidad", 0) or 0) <= 0:
			continue
		tags_list = p.get("etiquetas")
		if not isinstance(tags_list, list):
			tags_list = parse_etiquetas_list(tags_list)
		tags_norm = {normalize_text(x) for x in tags_list if x}
		pname = normalize_text(p.get("nombre_producto", ""))
		score = 0
		for t in toks:
			if t in tags_norm:
				score += 4
			elif any((len(nt) >= 4 and (t in nt or nt in t)) for nt in tags_norm):
				score += 2
			elif t in pname:
				score += 1
		if score > 0:
			scored.append((score, p))
	scored.sort(key=lambda x: -x[0])
	return [p for _, p in scored[:15]]


def obtener_producto_disponible_por_nombre(nombre_producto: str) -> dict | None:
	"""Busca un producto por nombre y verifica que tenga stock."""

	producto = obtener_producto_por_nombre(nombre_producto)
	if producto is None or producto["cantidad"] <= 0:
		return None
	return producto


def guardar_pedido(telegram_id: int, nombre: str, producto: str, cantidad: int) -> int:
	"""Compatibilidad: mantiene firma anterior para pickup presencial simple."""

	return crear_pedido(
		telegram_id=telegram_id,
		nombre=nombre,
		producto=producto,
		cantidad=cantidad,
		tipo_entrega="pickup",
		ubicacion_entrega=None,
		delivery_costo_usd=0,
		metodo_pago="presencial",
		comprobante_file_id=None,
	)


def crear_pedido(
	telegram_id: int,
	nombre: str,
	producto: str,
	cantidad: int,
	tipo_entrega: str,
	ubicacion_entrega: str | None,
	delivery_costo_usd: float,
	metodo_pago: str,
	comprobante_file_id: str | None,
	items: list[dict] | None = None,
	modo_precio: str | None = None,
) -> int:
	"""Crea pedido sin descontar stock. El descuento ocurre al concluir por admin."""

	items_normalizados: list[dict]
	if items:
		items_normalizados = _normalizar_items(items)
	else:
		producto_db = obtener_producto_por_nombre(producto)
		if producto_db is None:
			raise ValueError("El producto no existe en el catálogo.")
		if producto_db["cantidad"] < cantidad:
			raise ValueError("No hay stock suficiente para ese pedido.")
		items_normalizados = [
			{
				"producto_id": producto_db["id"],
				"cantidad": cantidad,
				"precio_unitario": float(
					obtener_resumen_montos(producto, cantidad, delivery_costo_usd=0, modo_precio=modo_precio)["precio_usd"]
				),
			}
		]

	productos_catalogo = {producto["id"]: producto for producto in listar_productos()}
	for item in items_normalizados:
		producto_db = productos_catalogo.get(item["producto_id"])
		if producto_db is None:
			raise ValueError("El producto no existe en el catálogo.")
		if producto_db["cantidad"] < item["cantidad"]:
			raise ValueError(f"No hay stock suficiente para {producto_db['nombre_producto']}.")

	usuario = obtener_usuario_por_telegram_id(telegram_id)
	if usuario is None:
		usuario_id = crear_usuario(nombre, telegram_id)
	else:
		usuario_id = usuario["id"]

	if delivery_costo_usd < 0:
		raise ValueError("El costo de delivery no puede ser negativo.")

	delivery_costo_real = delivery_costo_usd
	if tipo_entrega == "delivery":
		if delivery_costo_real <= 0:
			quote = obtener_costo_delivery_pendiente(telegram_id)
			if quote is None:
				raise ValueError(
					"Aun no hay costo de delivery asignado por el admin. Espera la revisión."
				)
			delivery_costo_real = float(quote)
		else:
			# Permitir override explícito si el flujo lo define.
			delivery_costo_real = float(delivery_costo_real)

	if tipo_entrega == "delivery" and not ubicacion_entrega:
		raise ValueError("Para delivery debes enviar ubicación de entrega.")

	if tipo_entrega == "delivery" and not comprobante_file_id:
		raise ValueError("Para delivery debes enviar comprobante de pago.")

	estado_inicial = "pendiente_admin" if tipo_entrega == "delivery" else "pendiente_pickup"

	pedido_id = insertar_pedido(
		usuario_id=usuario_id,
		producto_id=items_normalizados[0]["producto_id"],
		cantidad=sum(int(item["cantidad"]) for item in items_normalizados),
		tipo_entrega=tipo_entrega,
		ubicacion_entrega=ubicacion_entrega,
		delivery_costo_usd=delivery_costo_real,
		metodo_pago=metodo_pago,
		comprobante_file_id=comprobante_file_id,
		estado=estado_inicial,
		items=items_normalizados,
	)

	if tipo_entrega == "delivery":
		# Si el costo ya fue definido antes de crear el pedido, marcarlo como revisado.
		if delivery_costo_real > 0:
			actualizar_costo_delivery(pedido_id, float(delivery_costo_real))
		limpiar_costo_delivery_pendiente(telegram_id)

	return pedido_id


def admin_asignar_costo_delivery(pedido_id: int, delivery_costo_usd: float) -> dict:
	"""Admin asigna el costo de delivery a un pedido delivery pendiente."""

	if delivery_costo_usd < 0:
		raise ValueError("El costo de delivery no puede ser negativo.")

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	if pedido["tipo_entrega"] != "delivery":
		raise ValueError("Solo los pedidos delivery aceptan costo de delivery.")

	if pedido["estado"] != "pendiente_admin":
		raise ValueError("Solo se puede ajustar delivery en pedidos pendientes de admin.")

	actualizar_costo_delivery(pedido_id, float(delivery_costo_usd))
	pedido["delivery_costo_usd"] = float(delivery_costo_usd)
	pedido["delivery_revisado"] = 1
	return pedido


def registrar_mensaje_cliente(
	pedido_id: int,
	telegram_id: int,
	mensaje: str,
	polaridad: float | None = None,
) -> dict:
	"""Registra mensaje del cliente si el pedido le pertenece."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	if pedido["telegram_id"] != telegram_id:
		raise ValueError("No puedes escribir en un pedido que no te pertenece.")

	registrar_mensaje_pedido(pedido_id, "cliente", mensaje, polaridad=polaridad)
	return pedido


def registrar_mensaje_admin(pedido_id: int, mensaje: str) -> dict:
	"""Registra mensaje del administrador en la conversación del pedido."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	registrar_mensaje_pedido(pedido_id, "admin", mensaje)
	return pedido


def obtener_chat_admin(pedido_id: int, limite: int = 30) -> tuple[dict, list[dict]]:
	"""Obtiene pedido y su conversación para vista administrativa."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	chat = listar_chat_pedido(pedido_id, limite=limite)
	chat.reverse()
	return pedido, chat


def _descontar_stock_si_corresponde(pedido: dict) -> None:
	"""Descuenta stock una sola vez, cuando el pedido se concluye."""

	if pedido["stock_descontado"] == 1:
		return

	items = pedido.get("items") or obtener_items_pedido(pedido["id"])
	if not items:
		items = [
			{
				"producto_id": pedido["producto_id"],
				"cantidad": pedido["cantidad"],
			},
		]

	for item in items:
		if not descontar_stock(item["producto_id"], int(item["cantidad"])):
			raise ValueError("No hay stock suficiente para concluir este pedido.")

	marcar_stock_descontado(pedido["id"])


def admin_confirmar_delivery(pedido_id: int) -> dict:
	"""Admin confirma pedido delivery para iniciar entrega."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	if pedido["tipo_entrega"] != "delivery":
		raise ValueError("Este pedido no es delivery.")

	if pedido["estado"] != "pendiente_admin":
		raise ValueError("El pedido no está en estado pendiente_admin.")

	if pedido.get("delivery_revisado", 0) != 1:
		if float(pedido.get("delivery_costo_usd", 0) or 0) > 0:
			actualizar_costo_delivery(pedido_id, float(pedido["delivery_costo_usd"]))
			pedido["delivery_revisado"] = 1
		else:
			raise ValueError(
				"Debes revisar la ubicación y fijar costo de delivery antes de confirmar."
			)

	actualizar_estado_pedido(pedido_id, "en_camino")
	pedido["estado"] = "en_camino"
	return pedido


def admin_marcar_entregado_delivery(pedido_id: int) -> dict:
	"""Admin marca un delivery como entregado y descuenta stock."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	if pedido["tipo_entrega"] != "delivery":
		raise ValueError("Este pedido no es delivery.")

	if pedido["estado"] != "en_camino":
		raise ValueError("Para marcar entregado, el pedido debe estar en estado en_camino.")

	_descontar_stock_si_corresponde(pedido)
	actualizar_estado_pedido(pedido_id, "entregado")
	pedido["estado"] = "entregado"
	pedido["stock_descontado"] = 1
	return pedido


def admin_concluir_pickup(pedido_id: int) -> dict:
	"""Admin concluye pickup (pago presencial) y descuenta stock."""

	pedido = obtener_pedido_por_id(pedido_id)
	if pedido is None:
		raise ValueError("El pedido no existe.")

	if pedido["tipo_entrega"] != "pickup":
		raise ValueError("Este pedido no es pickup.")

	if pedido["estado"] != "pendiente_pickup":
		raise ValueError("El pedido no está en estado pendiente_pickup.")

	_descontar_stock_si_corresponde(pedido)
	actualizar_estado_pedido(pedido_id, "concluido_pickup")
	pedido["estado"] = "concluido_pickup"
	pedido["stock_descontado"] = 1
	return pedido


def obtener_pedidos_admin() -> list[dict]:
	"""Lista pedidos para revisión/acciones del administrador."""

	return listar_pedidos_admin()
