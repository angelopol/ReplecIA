"""
Panel web Flask (admin): pedidos, usuarios, métricas y acciones alineadas al dominio del bot.
Ejecutar desde esta carpeta: python app.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from src.core.services.pedidos_service import (
	admin_confirmar_delivery,
	admin_concluir_pickup,
	admin_marcar_entregado_delivery,
)
from src.core.telegram_notify import (
	TelegramNotifyError,
	enviar_mensaje_telegram,
	texto_cliente_pedido_concluido_pickup,
	texto_cliente_pedido_en_camino,
	texto_cliente_pedido_entregado,
)
from src.data.database import get_connection, initialize_database
from src.data.repositories.pedidos_repo import listar_chat_pedido, obtener_pedido_por_id
from src.data.repositories.usuarios_repo import listar_usuarios_panel_pagina

_VZ = ZoneInfo("America/Caracas")


def _parse_fecha_pedido(valor: str | None) -> datetime | None:
	if not valor:
		return None
	texto = str(valor).strip()
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
		try:
			return datetime.strptime(texto, fmt).replace(tzinfo=_VZ)
		except ValueError:
			continue
	return None


def _tabla_tiene_columna(conn, tabla: str, columna: str) -> bool:
	for col in conn.execute(f"PRAGMA table_info({tabla})"):
		if col[1] == columna:
			return True
	return False


def _parse_int_request(arg: str | None, default: int, min_v: int, max_v: int) -> int:
	try:
		n = int(str(arg or "").strip())
	except ValueError:
		return default
	return max(min_v, min(max_v, n))


def _parse_q_pedido_id_hash(q: str) -> int | None:
	"""Si la búsqueda es solo '#8' o '# 8', devuelve el id de pedido."""

	s = (q or "").strip()
	m = re.fullmatch(r"#\s*(\d+)\s*", s, flags=re.IGNORECASE)
	return int(m.group(1)) if m else None


def _count_alertas_sentimiento_total(conn) -> int:
	"""Pedidos distintos con al menos un mensaje de cliente con polaridad < -0.05."""

	if not _tabla_tiene_columna(conn, "pedido_chat", "polaridad_sentimiento"):
		return 0
	row = conn.execute(
		"SELECT COUNT(DISTINCT pedido_id) AS c FROM pedido_chat "
		"WHERE emisor = 'cliente' AND polaridad_sentimiento IS NOT NULL "
		"AND polaridad_sentimiento < -0.05"
	).fetchone()
	return int(row["c"] or 0) if row else 0


def _safe_redirect_path(next_val: str | None) -> str | None:
	"""Solo rutas relativas internas (evita open redirect)."""

	if not next_val:
		return None
	s = str(next_val).strip()
	if not s.startswith("/") or s.startswith("//"):
		return None
	if s.lower().startswith("/login"):
		return None
	return s


def _inicio_fin_hoy_caracas() -> tuple[datetime, datetime]:
	ahora = datetime.now(_VZ)
	inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
	fin = inicio + timedelta(days=1)
	return inicio, fin


def _count_clientes_nuevos_hoy(inicio: datetime, fin: datetime) -> int:
	"""Usuarios cuya fecha_registro cae en el día actual (America/Caracas)."""

	with get_connection() as conn:
		if not _tabla_tiene_columna(conn, "usuarios", "fecha_registro"):
			return 0
		n = 0
		for row in conn.execute(
			"SELECT fecha_registro FROM usuarios "
			"WHERE fecha_registro IS NOT NULL AND TRIM(fecha_registro) != ''"
		):
			ts = _parse_fecha_pedido(row["fecha_registro"])
			if ts and inicio <= ts < fin:
				n += 1
	return n


_TITULOS_ESTADO_METRICA: dict[str, str] = {
	"entregado": "Entregados",
	"en_camino": "En camino",
	"concluido_pickup": "Pickup concluido",
	"pendiente_admin": "Pendiente admin",
	"pendiente_pickup": "Pendiente pickup",
	"pendiente": "Pendiente",
}


def _tarjetas_metricas_por_estado(conteo: dict[str, int]) -> list[dict]:
	"""Orden fijo para las cajas del dashboard; incluye estados extra al final."""

	priority = (
		"entregado",
		"en_camino",
		"concluido_pickup",
		"pendiente_admin",
		"pendiente_pickup",
		"pendiente",
	)
	seen: set[str] = set()
	out: list[dict] = []
	for estado in priority:
		if estado not in conteo:
			continue
		out.append(
			{
				"estado": estado,
				"titulo": _TITULOS_ESTADO_METRICA.get(estado, estado.replace("_", " ").title()),
				"cantidad": int(conteo[estado]),
			}
		)
		seen.add(estado)
	for estado, n in sorted(conteo.items(), key=lambda x: x[0]):
		if estado in seen:
			continue
		out.append(
			{
				"estado": estado,
				"titulo": estado.replace("_", " ").title(),
				"cantidad": int(n),
			}
		)
	return out


def _ensure_admin_seed() -> None:
	user = os.getenv("PANEL_ADMIN_USER", "admin")
	pwd = os.getenv("PANEL_ADMIN_PASSWORD", "admin")
	with get_connection() as conn:
		n = conn.execute("SELECT COUNT(*) AS c FROM admin").fetchone()["c"]
		if int(n) == 0:
			h = generate_password_hash(pwd)
			conn.execute(
				'INSERT INTO admin (usuario, "contraseña") VALUES (?, ?)',
				(user, h),
			)
			conn.commit()


class AdminUser(UserMixin):
	def __init__(self, admin_id: int, usuario: str) -> None:
		self.id = str(admin_id)
		self.usuario = usuario


def _load_user(user_id: str) -> AdminUser | None:
	try:
		aid = int(user_id)
	except ValueError:
		return None
	with get_connection() as conn:
		row = conn.execute("SELECT id, usuario FROM admin WHERE id = ?", (aid,)).fetchone()
	if not row:
		return None
	return AdminUser(int(row["id"]), str(row["usuario"]))


def create_app() -> Flask:
	initialize_database()
	_ensure_admin_seed()

	app = Flask(__name__, template_folder=str(ROOT / "templates"))
	app.secret_key = os.getenv("FLASK_SECRET_KEY", "cambiar-en-produccion-flask-secret")

	login_manager = LoginManager(app)
	login_manager.login_view = "login"

	@login_manager.user_loader
	def load_user(uid: str) -> AdminUser | None:
		return _load_user(uid)

	@app.route("/login", methods=["GET", "POST"])
	def login():
		next_get = _safe_redirect_path(request.args.get("next"))
		if current_user.is_authenticated:
			return redirect(next_get or url_for("dashboard"))
		if request.method == "POST":
			usuario = (request.form.get("usuario") or "").strip()
			clave = request.form.get("contraseña") or ""
			with get_connection() as conn:
				row = conn.execute(
					'SELECT id, usuario, "contraseña" FROM admin WHERE usuario = ?',
					(usuario,),
				).fetchone()
			if row and check_password_hash(str(row["contraseña"]), clave):
				login_user(AdminUser(int(row["id"]), str(row["usuario"])), remember=True)
				next_post = _safe_redirect_path(request.form.get("next")) or next_get
				return redirect(next_post or url_for("dashboard"))
			flash("Usuario o contraseña incorrectos.", "error")
			return render_template(
				"login.html",
				next_url=_safe_redirect_path(request.form.get("next")) or next_get or "",
			)
		return render_template("login.html", next_url=next_get or "")

	@app.route("/logout")
	@login_required
	def logout():
		logout_user()
		return redirect(url_for("login"))

	@app.route("/guia-panel")
	@login_required
	def guia_panel():
		return render_template("panel_guia.html")

	@app.route("/")
	@login_required
	def dashboard():
		estado_f = (request.args.get("estado") or "").strip()
		q_raw = (request.args.get("q") or "").strip()
		limite = _parse_int_request(request.args.get("limite"), 200, 1, 500)
		solo_alerta = str(request.args.get("solo_alerta", "")).lower() in ("1", "on", "true", "yes", "si")
		inicio_hoy, fin_hoy = _inicio_fin_hoy_caracas()

		pedido_id_hash = _parse_q_pedido_id_hash(q_raw)
		q_busqueda = "" if pedido_id_hash is not None else q_raw

		ids_alerta_sentimiento: set[int] = set()
		total_alertas_sentimiento = 0
		with get_connection() as conn:
			total_alertas_sentimiento = _count_alertas_sentimiento_total(conn)
			if _tabla_tiene_columna(conn, "pedido_chat", "polaridad_sentimiento"):
				for r in conn.execute(
					"SELECT DISTINCT pedido_id FROM pedido_chat "
					"WHERE emisor = 'cliente' AND polaridad_sentimiento IS NOT NULL "
					"AND polaridad_sentimiento < -0.05"
				):
					ids_alerta_sentimiento.add(int(r["pedido_id"]))

		with get_connection() as conn:
			stats_rows = conn.execute(
				"SELECT p.id, p.estado, p.tipo_entrega, p.fecha_creacion, "
				"TRIM(COALESCE(u.nombre,'') || ' ' || COALESCE(u.apellido,'')) AS cliente, "
				"u.telegram_id, pr.nombre_producto, p.cantidad "
				"FROM pedidos p "
				"JOIN usuarios u ON u.id = p.usuario_id "
				"LEFT JOIN productos pr ON pr.id = p.producto_id "
				"ORDER BY p.id DESC LIMIT 2000"
			).fetchall()
		pedidos_stats = [dict(r) for r in stats_rows]

		hoy_count = 0
		for p in pedidos_stats:
			ts = _parse_fecha_pedido(p.get("fecha_creacion"))
			if ts and inicio_hoy <= ts < fin_hoy:
				hoy_count += 1

		conteo_estado: dict[str, int] = {}
		for p in pedidos_stats:
			e = str(p.get("estado") or "")
			conteo_estado[e] = conteo_estado.get(e, 0) + 1

		where_parts = ["1=1"]
		params_sql: list = []
		if estado_f:
			where_parts.append("p.estado = ?")
			params_sql.append(estado_f)
		if pedido_id_hash is not None:
			where_parts.append("p.id = ?")
			params_sql.append(pedido_id_hash)
		if solo_alerta:
			where_parts.append(
				"EXISTS (SELECT 1 FROM pedido_chat c WHERE c.pedido_id = p.id "
				"AND c.emisor = 'cliente' AND c.polaridad_sentimiento IS NOT NULL "
				"AND c.polaridad_sentimiento < -0.05)"
			)

		sql_from = (
			"SELECT p.id, p.estado, p.tipo_entrega, p.fecha_creacion, "
			"TRIM(COALESCE(u.nombre,'') || ' ' || COALESCE(u.apellido,'')) AS cliente, "
			"u.telegram_id, pr.nombre_producto, p.cantidad "
			"FROM pedidos p "
			"JOIN usuarios u ON u.id = p.usuario_id "
			"LEFT JOIN productos pr ON pr.id = p.producto_id "
			f"WHERE {' AND '.join(where_parts)} ORDER BY p.id DESC "
		)

		if q_busqueda:
			fetch_n = min(3000, max(limite * 50, 500))
			sql = sql_from + "LIMIT ?"
			params_run = [*params_sql, fetch_n]
		else:
			sql = sql_from + "LIMIT ?"
			params_run = [*params_sql, limite]

		with get_connection() as conn:
			pedidos_rows = conn.execute(sql, params_run).fetchall()
		pedidos = [dict(r) for r in pedidos_rows]

		for p in pedidos:
			p["alerta_sentimiento_negativo"] = int(p["id"]) in ids_alerta_sentimiento

		if q_busqueda:
			needle = q_busqueda.lower()

			def _blob_pedido(p: dict) -> str:
				pid = str(p.get("id") or "")
				partes = [
					pid,
					f"#{pid}",
					str(p.get("estado") or ""),
					str(p.get("tipo_entrega") or ""),
					str(p.get("cliente") or ""),
					str(p.get("nombre_producto") or ""),
					str(p.get("telegram_id") or ""),
				]
				return " ".join(partes).lower()

			pedidos = [p for p in pedidos if needle in _blob_pedido(p)]
			pedidos = pedidos[:limite]

		alertas: list[dict] = []
		with get_connection() as conn:
			if _tabla_tiene_columna(conn, "pedido_chat", "polaridad_sentimiento"):
				for r in conn.execute(
					"SELECT pedido_id, MAX(fecha_creacion) AS ultima "
					"FROM pedido_chat "
					"WHERE emisor = 'cliente' AND polaridad_sentimiento IS NOT NULL "
					"AND polaridad_sentimiento < -0.05 "
					"GROUP BY pedido_id ORDER BY ultima DESC LIMIT 15"
				):
					alertas.append({"pedido_id": int(r["pedido_id"]), "ultima": r["ultima"]})

		clientes_nuevos_hoy = _count_clientes_nuevos_hoy(inicio_hoy, fin_hoy)

		return render_template(
			"dashboard.html",
			pedidos=pedidos,
			estado_f=estado_f,
			q=q_raw,
			limite=limite,
			solo_alerta=solo_alerta,
			hoy_count=hoy_count,
			clientes_nuevos_hoy=clientes_nuevos_hoy,
			conteo_estado=conteo_estado,
			tarjetas_estado=_tarjetas_metricas_por_estado(conteo_estado),
			total_pedidos_lista=len(pedidos),
			num_alertas_sentimiento=total_alertas_sentimiento,
			alertas=alertas,
			zona_caracas="America/Caracas",
		)

	@app.route("/pedido/<int:pedido_id>")
	@login_required
	def pedido_detalle(pedido_id: int):
		pedido = obtener_pedido_por_id(pedido_id)
		if not pedido:
			flash("Pedido no encontrado.", "error")
			return redirect(url_for("dashboard"))
		chat = listar_chat_pedido(pedido_id, limite=80)
		chat.reverse()
		return render_template("pedido_detalle.html", pedido=pedido, chat=chat)

	@app.route("/pedido/<int:pedido_id>/accion", methods=["POST"])
	@login_required
	def pedido_accion(pedido_id: int):
		accion = (request.form.get("accion") or "").strip()
		pedido = obtener_pedido_por_id(pedido_id)
		if not pedido:
			flash("Pedido no encontrado.", "error")
			return redirect(url_for("dashboard"))
		texto_cliente = ""
		try:
			if accion == "confirmar":
				pedido = admin_confirmar_delivery(pedido_id)
				texto_cliente = texto_cliente_pedido_en_camino(pedido_id)
				flash("Pedido confirmado para entrega (en camino).", "ok")
			elif accion == "entregado":
				pedido = admin_marcar_entregado_delivery(pedido_id)
				texto_cliente = texto_cliente_pedido_entregado(pedido_id)
				flash("Pedido marcado como entregado.", "ok")
			elif accion == "concluir":
				pedido = admin_concluir_pickup(pedido_id)
				texto_cliente = texto_cliente_pedido_concluido_pickup(pedido_id)
				flash("Pedido pickup concluido.", "ok")
			else:
				flash("Acción no válida.", "error")
				return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
		except ValueError as exc:
			flash(str(exc), "error")
			return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

		if texto_cliente:
			try:
				enviar_mensaje_telegram(int(pedido["telegram_id"]), texto_cliente)
			except TelegramNotifyError as exc:
				flash(f"Estado actualizado, pero no se pudo notificar al cliente por Telegram: {exc}", "error")

		return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

	@app.route("/usuarios")
	@login_required
	def usuarios():
		pagina = _parse_int_request(request.args.get("page"), 1, 1, 50_000)
		por_pagina = _parse_int_request(request.args.get("per_page"), 50, 10, 100)
		rows, total = listar_usuarios_panel_pagina(pagina, por_pagina)
		total_paginas = max(1, (total + por_pagina - 1) // por_pagina) if total else 1
		return render_template(
			"usuarios.html",
			usuarios=rows,
			page=pagina,
			per_page=por_pagina,
			total_usuarios=total,
			total_pages=total_paginas,
		)

	return app


app = create_app()

if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("FLASK_PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
