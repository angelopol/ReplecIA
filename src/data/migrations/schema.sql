PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	nombre TEXT NOT NULL,
	apellido TEXT NOT NULL DEFAULT '',
	cedula TEXT NOT NULL DEFAULT '',
	telefono TEXT,
	username_telegram TEXT,
	telegram_id INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS productos (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	nombre_producto TEXT NOT NULL UNIQUE,
	precio_detal REAL NOT NULL DEFAULT 0,
	precio_mayor REAL NOT NULL DEFAULT 0,
	cantidad INTEGER NOT NULL,
	descripcion TEXT NOT NULL DEFAULT '',
	etiquetas TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS pedidos (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	usuario_id INTEGER NOT NULL,
	producto_id INTEGER NOT NULL,
	cantidad INTEGER NOT NULL,
	tipo_entrega TEXT NOT NULL,
	ubicacion_entrega TEXT,
	delivery_costo_usd REAL NOT NULL DEFAULT 0,
	delivery_revisado INTEGER NOT NULL DEFAULT 0,
	metodo_pago TEXT NOT NULL,
	comprobante_file_id TEXT,
	estado TEXT NOT NULL,
	stock_descontado INTEGER NOT NULL DEFAULT 0,
	fecha_creacion TEXT NOT NULL DEFAULT (datetime('now')),
	FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
	FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS pedido_items (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	pedido_id INTEGER NOT NULL,
	producto_id INTEGER NOT NULL,
	cantidad INTEGER NOT NULL,
	precio_unitario REAL NOT NULL,
	FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,
	FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS pedido_chat (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	pedido_id INTEGER NOT NULL,
	emisor TEXT NOT NULL,
	mensaje TEXT NOT NULL,
	fecha_creacion TEXT NOT NULL DEFAULT (datetime('now')),
	FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_config (
	config_key TEXT PRIMARY KEY,
	config_value TEXT NOT NULL
);
