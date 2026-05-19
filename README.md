# ReplecIA

ReplecIA es una app web SaaS para tiendas de autopartes: inventario, compatibilidad vehicular, chatbot IA y gestión de pedidos lista para desplegar en Vercel.

## Stack

- Next.js + TypeScript
- Drizzle ORM
- Postgres serverless vía Neon/Vercel Marketplace
- Gemini como proveedor IA inicial, encapsulado en `lib/ai/provider.ts`

## Configuración

1. Instala dependencias:

```bash
npm install
```

2. Crea `.env.local` usando `.env.example`.

3. Crea una base Postgres en Neon desde Vercel Marketplace y copia `DATABASE_URL`.

4. Aplica esquema y datos demo:

```bash
npm run db:push
npm run db:seed
```

5. Ejecuta localmente:

```bash
npm run dev
```

## Rutas

- `/` chat web público
- `/admin` dashboard
- `/admin/inventory` inventario y compatibilidad
- `/admin/orders` pedidos
- `/admin/conversations` conversaciones del asistente
- `/api/chat` endpoint del asistente IA
- `/api/orders` creación de pedidos

## Deploy En Vercel

Configura estas variables en Vercel:

- `DATABASE_URL`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `NEXTAUTH_SECRET`
- `ADMIN_SEED_EMAIL`
- `ADMIN_SEED_PASSWORD`

Luego ejecuta `npm run db:push` y `npm run db:seed` contra la base de producción desde tu entorno local o desde un job seguro.
