import { and, desc, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { orderItems, orders, products } from "@/lib/db/schema";
import { createOrderSchema, updateOrderStatusSchema } from "@/lib/validators/order";

export async function listOrders(businessId: string) {
  return db.query.orders.findMany({
    where: eq(orders.businessId, businessId),
    with: {
      items: {
        with: {
          product: true,
        },
      },
    },
    orderBy: desc(orders.createdAt),
  });
}

export async function createOrder(businessId: string, input: unknown) {
  const parsed = createOrderSchema.parse(input);
  const product = await db.query.products.findFirst({
    where: and(eq(products.id, parsed.productId), eq(products.businessId, businessId)),
  });

  if (!product) {
    throw new Error("Producto no encontrado");
  }

  if (product.stock < parsed.quantity) {
    throw new Error("No hay stock suficiente para crear el pedido");
  }

  const total = Number(product.priceUsd) * parsed.quantity;

  const [order] = await db
    .insert(orders)
    .values({
      businessId,
      conversationId: parsed.conversationId,
      customerName: parsed.customerName,
      customerPhone: parsed.customerPhone,
      notes: parsed.notes || "",
      totalUsd: total.toFixed(2),
      status: "quote_requested",
    })
    .returning();

  await db.insert(orderItems).values({
    orderId: order.id,
    productId: product.id,
    quantity: parsed.quantity,
    unitPriceUsd: product.priceUsd,
  });

  return order;
}

export async function updateOrderStatus(input: unknown) {
  const parsed = updateOrderStatusSchema.parse(input);
  const [order] = await db
    .update(orders)
    .set({ status: parsed.status, updatedAt: sql`now()` })
    .where(eq(orders.id, parsed.orderId))
    .returning();

  if (parsed.status === "confirmed") {
    const items = await db.query.orderItems.findMany({
      where: eq(orderItems.orderId, parsed.orderId),
      with: { product: true },
    });

    for (const item of items) {
      await db
        .update(products)
        .set({
          stock: Math.max(0, item.product.stock - item.quantity),
          updatedAt: sql`now()`,
        })
        .where(eq(products.id, item.productId));
    }
  }

  return order;
}
