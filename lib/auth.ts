import "server-only";

import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";
import type { AdminSession } from "@/lib/auth-types";
export { hashPassword, verifyPassword } from "@/lib/password";

const COOKIE_NAME = "replecia_session";
const encoder = new TextEncoder();

function secretKey() {
  const secret = process.env.NEXTAUTH_SECRET;
  if (!secret) {
    throw new Error("NEXTAUTH_SECRET is required");
  }
  return encoder.encode(secret);
}

export async function createAdminSession(session: AdminSession) {
  return new SignJWT(session)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("7d")
    .sign(secretKey());
}

export async function verifyAdminSession(token: string): Promise<AdminSession | null> {
  try {
    const { payload } = await jwtVerify(token, secretKey());
    if (!payload.adminId || !payload.businessId || !payload.email) return null;
    return {
      adminId: String(payload.adminId),
      businessId: String(payload.businessId),
      email: String(payload.email),
    };
  } catch {
    return null;
  }
}

export async function getCurrentSession() {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  return token ? verifyAdminSession(token) : null;
}

export async function setSessionCookie(session: AdminSession) {
  const token = await createAdminSession(session);
  (await cookies()).set(COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
}

export async function clearSessionCookie() {
  (await cookies()).delete(COOKIE_NAME);
}
