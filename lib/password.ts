import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

export function hashPassword(password: string) {
  const salt = randomBytes(16).toString("hex");
  const hash = scryptSync(password, salt, 64).toString("hex");
  return `${salt}:${hash}`;
}

export function verifyPassword(password: string, stored: string) {
  const [salt, key] = stored.split(":");
  if (!salt || !key) return false;
  const candidate = Buffer.from(scryptSync(password, salt, 64).toString("hex"), "hex");
  const expected = Buffer.from(key, "hex");
  return candidate.length === expected.length && timingSafeEqual(candidate, expected);
}
