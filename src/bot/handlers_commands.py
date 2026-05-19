from telegram import Update
from telegram.ext import ContextTypes

from ..config.settings import ADMIN_TELEGRAM_ID, PAGO_MOVIL_TELEFONO
from ..data.repositories.usuarios_repo import (
	asegurar_usuario_telegram,
	nombre_publico_usuario,
	obtener_usuario_por_telegram_id,
	usuario_perfil_completo,
)


def _es_operador_telegram(update: Update) -> bool:
	"""True solo si el chat corresponde al ID de admin configurado en .env."""

	u = update.effective_user
	if u is None or ADMIN_TELEGRAM_ID is None:
		return False
	return u.id == ADMIN_TELEGRAM_ID


_TEXTO_AYUDA_CLIENTE = (
	"Helados Cali — Ayuda para clientes\n\n"
	"Cómo pedir\n"
	"• Puede escribir en lenguaje natural lo que desea (ej.: «quiero 4 barquillas de chocolate»). "
	"El bot entiende productos y cantidades según el catálogo.\n"
	"• O use /pedido para un flujo paso a paso con botones: productos, cantidad, tipo de entrega y pago.\n\n"
	"Antes de confirmar\n"
	"• Elegir precios al detalle o al mayor (si el pedido al mayor no alcanza el mínimo, el bot se lo indicará).\n"
	"• Retiro en tienda o envío a domicilio. Si es domicilio, envíe la dirección completa o comparta la ubicación en el mapa.\n"
	"• Le avisaremos el costo de envío cuando corresponda; luego elija método de pago y revise el resumen.\n"
	"• Para pago móvil en delivery, deberá enviar la foto del comprobante cuando se lo pidamos.\n\n"
	"Otros comandos útiles\n"
	"• /inicio o /comenzar — mensaje de bienvenida (en muchas apps también aparece como /start).\n"
	"• /ayuda, /guia o /comandos — esta guía.\n"
	"• /mis_datos — actualizar nombre, cédula o teléfono.\n"
	"• /cancelar — salir del paso a paso si está armando un pedido.\n"
	"• /soporte <id_pedido> <mensaje> — consulta sobre un pedido ya registrado "
	"(ej.: /soporte 15 Cambio de horario para retiro).\n\n"
	"También puede preguntar precios o catálogo escribiendo en el chat según las indicaciones del bot.\n"
	f"Si necesita hablar con la tienda por otro canal, puede usar el contacto configurado para pagos/mensajes: {PAGO_MOVIL_TELEFONO}."
)


_TEXTO_AYUDA_ADMIN_INTRO = (
	"Helados Cali — Ayuda para operador (admin)\n\n"
	"Qué hace el bot\n"
	"• Los clientes piden por chat; usted recibe avisos con botones para comprobante, confirmar envío, marcar entregado o concluir pickup.\n"
	"• En domicilio: cuando el cliente manda la ubicación, debe indicar el costo de envío en USD (solo el número en respuesta o /admin_ubicacion según el flujo).\n"
	"• Tiene además un panel web para ver pedidos, historial y alertas de sentimiento (misma base de datos que el bot).\n\n"
	"Modo administrativo en Telegram\n"
	"• /admin — activa el modo operador: órdenes en texto y comandos /admin_* se interpretan como gestión.\n"
	"• /salir_admin — sus mensajes vuelven a tratarse como los de un cliente (útil si usa la misma cuenta).\n\n"
	"A continuación: resumen para operador y lista de comandos de administración."
)


_TEXTO_AYUDA_ADMIN_COMANDOS = (
	"Comandos de administración\n\n"
	"Pedidos y cliente\n"
	" /admin_pedidos — listado resumido\n"
	" /admin_confirmar <id>\n"
	" /admin_entregado <id>\n"
	" /admin_concluir <id>\n"
	" /admin_comprobante <id>\n"
	" /admin_chat <id>\n"
	" /admin_responder <id> <mensaje>\n"
	" /admin_ubicacion <id_pedido> <costo_usd>\n\n"
	"Pago móvil (datos mostrados al cliente)\n"
	" /admin_ver_pago_movil\n"
	" /admin_set_pago_movil <telefono> <cedula> <banco>\n\n"
	"Catálogo\n"
	" /admin_productos\n"
	" /admin_add_producto <nombre> | <precio_detal_usd> | <precio_mayor_usd> | <stock>\n"
	" /admin_set_precios <nombre> | <precio_detal_usd> | <precio_mayor_usd>\n"
	" /admin_set_stock <nombre> | <stock>\n"
	" /admin_del_producto <nombre>\n"
	" /admin_set_descripcion <nombre> | <texto>\n"
	" /admin_set_etiquetas <nombre> | tag1, tag2, ...\n"
	" /admin_etiqueta_add <nombre> | <etiqueta>\n"
	" /admin_etiqueta_del <nombre> | <etiqueta>\n"
	" /admin_etiquetas_ver <nombre>\n"
	" /admin_inferir_etiquetas\n\n"
	"Aliases: /admin_nuevo_producto, /admin_editar_precio, /admin_editar_stock, /admin_eliminar_producto\n\n"
	"En modo /admin también puede usar frases cortas (ej. «ayuda», listar pedidos); "
	"use /ayuda, /guia o /comandos para ver esta referencia."
)


async def start(update: Update, econtext: ContextTypes.DEFAULT_TYPE) -> None:
	"""Comando /inicio (también /comenzar y /start): bienvenida y explicación breve."""

	user = update.effective_user
	if user:
		asegurar_usuario_telegram(user.id, user.username)
		u = obtener_usuario_por_telegram_id(user.id)
		nombre = nombre_publico_usuario(u) if u else ""
		if nombre and usuario_perfil_completo(user.id):
			await update.message.reply_text(
				f"Buen día, {nombre}. Gracias por contactarnos.\n"
				"Soy el asistente de pedidos de Helados Cali. Puede escribir su pedido en lenguaje natural "
				"(por ejemplo: «quiero 8 helados de chocolate») o usar /pedido para un flujo paso a paso.\n"
				"Comandos y guía rápida: /ayuda, /guia o /comandos."
			)
			return
	await update.message.reply_text(
		"Buen día. Gracias por contactarnos; soy el asistente de pedidos de Helados Cali.\n"
		"Para procesar su compra necesitamos algunos datos. Envíe un mensaje con lo que desea o use /pedido "
		"y le iremos guiando.\n"
		"Guía de uso: /ayuda, /guia o /comandos."
	)


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Comando /ayuda (también /guia y /comandos).

	Los clientes reciben solo la guía de uso. El operador recibe esa misma guía
	y después la sección de administración (tres mensajes).
	"""

	msg = update.message
	if msg is None:
		return

	if _es_operador_telegram(update):
		await msg.reply_text(_TEXTO_AYUDA_CLIENTE)
		await msg.reply_text(_TEXTO_AYUDA_ADMIN_INTRO)
		await msg.reply_text(_TEXTO_AYUDA_ADMIN_COMANDOS)
		return

	await msg.reply_text(_TEXTO_AYUDA_CLIENTE)
