import logging
from warnings import filterwarnings

from telegram.ext import (
	ApplicationBuilder,
	CallbackQueryHandler,
	CommandHandler,
	ConversationHandler,
	ContextTypes,
	MessageHandler,
	filters,
)
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning

# Flujo mixto (comandos/texto + inline): per_message=False es el ajuste válido; PTB avisa igual (FAQ per_*).
filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

from ..config.settings import ADMIN_TELEGRAM_ID, TELEGRAM_TOKEN
from ..data.database import initialize_database
from ..core.services.pedidos_service import prefetch_bcv_tasa_en_background
from .handlers_commands import start, ayuda
from .handlers_conversation import (
	admin_concluir,
	admin_chat,
	admin_confirmar,
	admin_accion_callback,
	admin_entregado,
	admin_listar_pedidos,
	admin_comprobante,
	admin_responder,
	admin_set_pago_movil,
	admin_ver_pago_movil,
	admin_productos,
	admin_add_producto,
	admin_set_precios,
	admin_del_producto,
	admin_set_descripcion,
	admin_set_etiquetas,
	admin_etiqueta_add,
	admin_etiqueta_del,
	admin_etiquetas_ver,
	admin_inferir_etiquetas,
	admin_set_precio,
	admin_set_stock,
	admin_ubicacion,
	admin_modo,
	admin_salir_modo,
	cliente_soporte,
	iniciar_pedido,
	iniciar_mis_datos,
	recibir_producto,
	recibir_cantidad,
	recibir_tipo_entrega,
	recibir_ubicacion,
	esperar_costo_delivery,
	handle_free_text,
	recibir_metodo_pago,
	recibir_modo_precio,
	recibir_comprobante,
	confirmar_pedido,
	cancelar_pedido,
	pedido_cliente_callback,
	recibir_nombre_cliente,
	recibir_cedula_cliente,
	recibir_telefono_cliente,
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

_log = logging.getLogger(__name__)


async def _log_ptb_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""Registra excepciones no tratadas (p. ej. timeouts hacia la API de Telegram)."""

	if isinstance(context.error, (TimedOut, NetworkError)):
		_log.warning("Fallo temporal de red con Telegram en update %s: %s", update, context.error)
		return
	_log.error("Excepción en update %s", update, exc_info=context.error)


def main() -> None:
	"""Punto de entrada del bot (Etapa 1).

	- Crea la Application de python-telegram-bot.
	- Registra saludo (/inicio, /comenzar, /start) y ayuda (/ayuda, /guia, /comandos).
	- Inicia el polling para escuchar mensajes de Telegram.
	"""

	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s: %(message)s",
	)
	# Evita imprimir URLs con el token del bot en cada request a api.telegram.org
	logging.getLogger("httpx").setLevel(logging.WARNING)
	logging.getLogger("httpcore").setLevel(logging.WARNING)
	_log.info("Iniciando bot (polling). Para detener: Ctrl+C en esta consola.")

	application = (
		ApplicationBuilder()
		.token(TELEGRAM_TOKEN)
		.request(HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0))
		.build()
	)
	application.add_error_handler(_log_ptb_error)
	initialize_database()
	prefetch_bcv_tasa_en_background()

	# Comandos básicos (español + /start por compatibilidad con el botón de Telegram)
	for _cmd in ("inicio", "comenzar", "start"):
		application.add_handler(CommandHandler(_cmd, start))
	for _cmd in ("ayuda", "guia", "comandos"):
		application.add_handler(CommandHandler(_cmd, ayuda))

	_pedido_cb = CallbackQueryHandler(pedido_cliente_callback, pattern=r"^pedido:(modo|entrega|pago):")

	# Registrar el flujo de conversación del pedido
	pedido_handler = ConversationHandler(
		entry_points=[
			CommandHandler("pedido", iniciar_pedido),
			CommandHandler("mis_datos", iniciar_mis_datos),
			MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text),
		],
		states={
			PIDIENDO_NOMBRE_CLIENTE: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre_cliente),
			],
			PIDIENDO_CEDULA_CLIENTE: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cedula_cliente),
			],
			PIDIENDO_TELEFONO_CLIENTE: [
				MessageHandler(filters.CONTACT, recibir_telefono_cliente),
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_telefono_cliente),
			],
			PIDIENDO_MODO_PRECIO: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_modo_precio),
				_pedido_cb,
			],
			PIDIENDO_PRODUCTO: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text),
				_pedido_cb,
			],
			PIDIENDO_CANTIDAD: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cantidad),
				_pedido_cb,
			],
			PIDIENDO_TIPO_ENTREGA: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_tipo_entrega),
				_pedido_cb,
			],
			PIDIENDO_UBICACION: [
				MessageHandler(filters.LOCATION, recibir_ubicacion),
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_ubicacion),
				_pedido_cb,
			],
			ESPERANDO_COSTO_DELIVERY: [
				MessageHandler((filters.LOCATION | (filters.TEXT & ~filters.COMMAND)), esperar_costo_delivery),
				_pedido_cb,
			],
			PIDIENDO_METODO_PAGO: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_metodo_pago),
				_pedido_cb,
			],
			PIDIENDO_COMPROBANTE: [
				MessageHandler(filters.PHOTO, recibir_comprobante),
				MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text),
				_pedido_cb,
			],
			CONFIRMANDO_PEDIDO: [
				MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_pedido),
				_pedido_cb,
			],
		},
		fallbacks=[
			CommandHandler("inicio", start),
			CommandHandler("comenzar", start),
			CommandHandler("start", start),
			CommandHandler("ayuda", ayuda),
			CommandHandler("guia", ayuda),
			CommandHandler("comandos", ayuda),
			CommandHandler("cancelar", cancelar_pedido),
			CommandHandler("pedido", iniciar_pedido),
			CommandHandler("mis_datos", iniciar_mis_datos),
		],
		allow_reentry=False,
	)
	application.add_handler(pedido_handler)
	# Si la conversación terminó por error pero quedaron botones `pedido:*`, aún así contestar el callback.
	application.add_handler(
		CallbackQueryHandler(pedido_cliente_callback, pattern=r"^pedido:(modo|entrega|pago):")
	)

	# Comandos administrativos
	application.add_handler(CommandHandler("admin", admin_modo))
	application.add_handler(CommandHandler("salir_admin", admin_salir_modo))
	application.add_handler(CommandHandler("admin_pedidos", admin_listar_pedidos))
	application.add_handler(CommandHandler("admin_confirmar", admin_confirmar))
	application.add_handler(CommandHandler("admin_entregado", admin_entregado))
	application.add_handler(CommandHandler("admin_concluir", admin_concluir))
	application.add_handler(CommandHandler("admin_comprobante", admin_comprobante))
	application.add_handler(CommandHandler("admin_chat", admin_chat))
	application.add_handler(CommandHandler("admin_responder", admin_responder))
	application.add_handler(CommandHandler("admin_ver_pago_movil", admin_ver_pago_movil))
	application.add_handler(CommandHandler("admin_set_pago_movil", admin_set_pago_movil))
	application.add_handler(CommandHandler("admin_productos", admin_productos))
	application.add_handler(CommandHandler("admin_add_producto", admin_add_producto))
	application.add_handler(CommandHandler("admin_set_precios", admin_set_precios))
	application.add_handler(CommandHandler("admin_set_precio", admin_set_precio))
	application.add_handler(CommandHandler("admin_set_stock", admin_set_stock))
	application.add_handler(CommandHandler("admin_ubicacion", admin_ubicacion))
	application.add_handler(CommandHandler("admin_del_producto", admin_del_producto))
	application.add_handler(CommandHandler("admin_set_descripcion", admin_set_descripcion))
	application.add_handler(CommandHandler("admin_set_etiquetas", admin_set_etiquetas))
	application.add_handler(CommandHandler("admin_etiqueta_add", admin_etiqueta_add))
	application.add_handler(CommandHandler("admin_etiqueta_del", admin_etiqueta_del))
	application.add_handler(CommandHandler("admin_etiquetas_ver", admin_etiquetas_ver))
	application.add_handler(CommandHandler("admin_inferir_etiquetas", admin_inferir_etiquetas))

	# Aliases en español para administración de productos
	application.add_handler(CommandHandler("admin_nuevo_producto", admin_add_producto))
	application.add_handler(CommandHandler("admin_editar_precio", admin_set_precio))
	application.add_handler(CommandHandler("admin_editar_stock", admin_set_stock))
	application.add_handler(CommandHandler("admin_eliminar_producto", admin_del_producto))
	application.add_handler(CallbackQueryHandler(admin_accion_callback, pattern=r"^admin:(confirmar|entregado|concluir|comprobante):\d+$"))

	# Soporte cliente-admin por pedido
	application.add_handler(CommandHandler("soporte", cliente_soporte))

	# Mantener el bot encendido escuchando mensajes
	application.run_polling()


if __name__ == "__main__":
	main()
