import { eq, sql } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { checkoutSessions, conversations, payments } from "@/lib/db/schema";
import { createOrder } from "@/lib/services/orders";

const confirmSchema = z.object({
  checkoutSessionId: z.string().uuid(),
  card: z.object({
    number: z.string().min(13),
    name: z.string().min(3),
    expiry: z.string().regex(/^\d{2}\/\d{2}$/),
    cvc: z.string().min(3).max(4),
  }),
});

export async function POST(request: Request) {
  try {
    const parsed = confirmSchema.parse(await request.json());
    const checkout = await db.query.checkoutSessions.findFirst({
      where: eq(checkoutSessions.id, parsed.checkoutSessionId),
      with: { product: true },
    });
    if (!checkout) throw new Error("Sesión de pago no encontrada.");
    if (checkout.status === "succeeded") throw new Error("Este pago ya fue aprobado.");
    if (!checkout.customerName || !checkout.customerPhone || !checkout.deliveryAddress) {
      throw new Error("Faltan datos de cliente o entrega.");
    }
    if (checkout.product.stock < checkout.quantity) throw new Error("No hay stock suficiente para completar la compra.");

    await db
      .update(checkoutSessions)
      .set({ status: "processing", updatedAt: sql`now()` })
      .where(eq(checkoutSessions.id, checkout.id));

    const paymentReference = `PAY-${Date.now().toString(36).toUpperCase()}`;
    const cardLast4 = parsed.card.number.replace(/\D/g, "").slice(-4);
    const order = await createOrder(checkout.businessId, {
      conversationId: checkout.conversationId,
      customerName: checkout.customerName,
      customerPhone: checkout.customerPhone,
      productId: checkout.productId,
      quantity: checkout.quantity,
      deliveryAddress: checkout.deliveryAddress,
      paymentReference,
      notes: "Pago confirmado desde la pasarela del chatbot. Coordinar entrega con el cliente.",
    });

    const [payment] = await db
      .insert(payments)
      .values({
        checkoutSessionId: checkout.id,
        orderId: order.id,
        status: "paid",
        amountUsd: checkout.amountUsd,
        reference: paymentReference,
        cardLast4,
        paidAt: sql`now()`,
      })
      .returning();

    await db
      .update(checkoutSessions)
      .set({ status: "succeeded", updatedAt: sql`now()` })
      .where(eq(checkoutSessions.id, checkout.id));

    const conversation = await db.query.conversations.findFirst({
      where: eq(conversations.id, checkout.conversationId),
    });
    await db
      .update(conversations)
      .set({
        currentOrderId: order.id,
        salesMemory: {
          ...(conversation?.salesMemory ?? {}),
          stage: "completed",
          lastOrderId: order.id,
          lastPaymentReference: paymentReference,
          customerName: checkout.customerName,
          customerPhone: checkout.customerPhone,
          deliveryAddress: checkout.deliveryAddress,
          checkoutSessionId: checkout.id,
        },
        updatedAt: sql`now()`,
      })
      .where(eq(conversations.id, checkout.conversationId));

    return NextResponse.json({
      order,
      payment,
      receipt: {
        orderId: order.id,
        reference: paymentReference,
        cardLast4,
        amountUsd: checkout.amountUsd,
      },
    });
  } catch (error) {
    return NextResponse.json(
      { error: "payment_failed", message: error instanceof Error ? error.message : "No se pudo confirmar el pago." },
      { status: 400 },
    );
  }
}
