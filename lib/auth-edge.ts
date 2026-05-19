import { jwtVerify } from "jose";
import type { AdminSession } from "@/lib/auth-types";

const encoder = new TextEncoder();

function secretKey() {
  const secret = process.env.NEXTAUTH_SECRET;
  if (!secret) {
    throw new Error("NEXTAUTH_SECRET is required");
  }
  return encoder.encode(secret);
}

export async function verifyAdminSessionEdge(token: string): Promise<AdminSession | null> {
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
