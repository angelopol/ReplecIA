import { eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { orders } from "@/lib/db/schema";

export async function GET(_request: Request, { params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await params;
  const order = await db.query.orders.findFirst({
    where: eq(orders.id, orderId),
    with: {
      items: {
        with: { product: true },
      },
    },
  });
  if (!order) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({
    order: {
      id: order.id,
      status: order.status,
      paymentStatus: order.paymentStatus,
      paymentReference: order.paymentReference,
      deliveryStatus: order.deliveryStatus,
      deliveryAddress: order.deliveryAddress,
      customerName: order.customerName,
      customerPhone: order.customerPhone,
      totalUsd: order.totalUsd,
      items: order.items.map((item) => ({
        quantity: item.quantity,
        productName: item.product.name,
        unitPriceUsd: item.unitPriceUsd,
      })),
    },
  });
}
