import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import * as schema from "@/lib/db/schema";

const connectionString = process.env.DATABASE_URL;

if (!connectionString && process.env.NODE_ENV !== "test") {
  console.warn("DATABASE_URL is not configured. Database-backed routes will fail until it is set.");
}

const sql = neon(connectionString ?? "postgres://user:password@localhost:5432/replecia");

export const db = drizzle(sql, { schema });
export { schema };
