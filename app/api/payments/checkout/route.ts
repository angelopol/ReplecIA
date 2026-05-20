import { eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { checkoutSessions } from "@/lib/db/schema";
import { getCheckoutSummary } from "@/lib/services/sales";

const checkoutSchema = z.object({
  checkoutSessionId: z.string().uuid(),
});

export async function POST(request: Request) {
  try {
    const parsed = checkoutSchema.parse(await request.json());
    const checkout = await getCheckoutSummary(parsed.checkoutSessionId);
    await db
      .update(checkoutSessions)
      .set({ status: "requires_payment_method" })
      .where(eq(checkoutSessions.id, parsed.checkoutSessionId));
    return NextResponse.json({ checkout });
  } catch (error) {
    return NextResponse.json(
      { error: "checkout_failed", message: error instanceof Error ? error.message : "No se pudo abrir el pago." },
      { status: 400 },
    );
  }
}
