import { and, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { checkoutSessions, orders, products, type SalesMemory } from "@/lib/db/schema";
import type { ProductMatch } from "@/lib/services/matching";

export type CommercialReply = {
  handled: boolean;
  reply?: string;
  memory: SalesMemory;
  checkout?: {
    id: string;
    status: string;
    amountUsd: string;
    productName: string;
    quantity: number;
  };
};

export function isPurchaseIntent(message: string) {
  return /compr|pagar|quiero|confirm|reserv|me lo llevo|lo quiero|cerrar|asegurar/i.test(message);
}

export function isGenericPurchaseIntent(message: string) {
  const normalized = message
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^\w\s]/g, " ")
    .replace(
      /\b(comprar|compra|pagar|pago|quiero|confirmar|confirmo|reservar|reserva|me|lo|la|llevo|cerrar|asegurar|ese|esa|este|esta|pieza|producto|unidad|unidades|por|favor)\b/g,
      " ",
    )
    .replace(/\b\d{1,2}\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  return isPurchaseIntent(message) && normalized.length === 0;
}

export function isStatusIntent(message: string) {
  return /pedido|orden|compra|compre|compré|entrega|delivery|pago|pague|pagué|estado|como va|seguimiento|recibo|detalle|datos|que fue|producto/i.test(
    message,
  );
}

export function isCancelIntent(message: string) {
  return /cancel|anular|olvida|no quiero|mejor no/i.test(message);
}

export function isVisualIdentificationIntent(message: string) {
  return /imagen|foto|ves|ver|reconoc|marca|modelo|que carro|que auto|no se cual/i.test(message);
}

export function isNewPurchaseAfterCompletedIntent(message: string) {
  return /otra compra|otro repuesto|otra pieza|nuevo pedido|nueva compra|comprar otra|comprar otro|buscar otra|buscar otro/i.test(
    message,
  );
}

export function parseQuantity(message: string) {
  const match = message.match(/\b(\d{1,2})\b/);
  return match ? Math.max(1, Number(match[1])) : 1;
}

export function parseContact(message: string) {
  const phone = message.match(/(?:\+?\d[\d\s().-]{6,}\d)/)?.[0]?.replace(/[^\d+]/g, "");
  const name = message
    .replace(/(?:\+?\d[\d\s().-]{6,}\d)/, "")
    .replace(/nombre|telefono|teléfono|soy|me llamo|mi nombre es/gi, "")
    .trim();
  return { customerName: name, customerPhone: phone };
}

export async function handleCommercialTurn(input: {
  businessId: string;
  conversationId: string;
  message: string;
  memory: SalesMemory;
  matches: ProductMatch[];
}): Promise<CommercialReply> {
  const { businessId, conversationId, message, matches } = input;
  let memory: SalesMemory = { stage: "idle", quantity: 1, ...input.memory };
  const purchaseIntent = isPurchaseIntent(message);
  const genericPurchaseIntent = isGenericPurchaseIntent(message);

  if (isVisualIdentificationIntent(message) && !purchaseIntent) {
    return { handled: false, memory: { ...memory, stage: memory.stage || "idle" } };
  }

  if (isCancelIntent(message)) {
    memory = { stage: "idle", quantity: 1 };
    return {
      handled: true,
      memory,
      reply: "Listo, cancelé el proceso actual. Puedo ayudarte a buscar otra pieza o iniciar una compra nueva.",
    };
  }

  if (isStatusIntent(message) && memory.lastOrderId && !(memory.stage === "completed" && isNewPurchaseAfterCompletedIntent(message))) {
    const orderSummary = await buildOrderStatusReply(memory);
    return {
      handled: true,
      memory,
      reply: orderSummary,
    };
  }

  if (memory.stage === "completed" && memory.lastOrderId && !isNewPurchaseAfterCompletedIntent(message)) {
    const orderSummary = await buildOrderStatusReply(memory);
    return {
      handled: true,
      memory,
      reply: `${orderSummary}\n\nSi quieres iniciar otra compra, dime "comprar otro repuesto" y buscamos una nueva pieza.`,
    };
  }

  if (memory.stage === "contact") {
    const contact = parseContact(message);
    if (!contact.customerPhone) {
      return {
        handled: true,
        memory,
        reply: "Perfecto, vamos a cerrar la compra. Envíame tu nombre y teléfono en un solo mensaje para reservar la pieza.",
      };
    }

    memory = {
      ...memory,
      customerName: contact.customerName || "Cliente ReplecIA",
      customerPhone: contact.customerPhone,
      stage: "delivery",
    };
    return {
      handled: true,
      memory,
      reply: "Datos recibidos. ¿Deseas delivery o retiro en tienda? Si es delivery, envíame la dirección completa para coordinar la entrega.",
    };
  }

  if (memory.stage === "delivery") {
    const deliveryAddress = /retiro|pickup|tienda|buscar/i.test(message) ? "Retiro en tienda" : message;
    const checkout = await createCheckoutFromMemory({
      businessId,
      conversationId,
      memory: { ...memory, deliveryAddress, stage: "payment" },
    });
    memory = {
      ...memory,
      deliveryAddress,
      stage: "payment",
      checkoutSessionId: checkout.id,
    };
    return {
      handled: true,
      memory,
      checkout,
      reply: `Listo. Reservo ${memory.selectedProductName} para ${deliveryAddress}. Total: $${checkout.amountUsd}. Completa el pago seguro para confirmar el pedido.`,
    };
  }

  if (memory.stage === "payment" && memory.checkoutSessionId) {
    return {
      handled: true,
      memory,
      checkout: await getCheckoutSummary(memory.checkoutSessionId),
      reply: "Tu compra está lista para pagar. Completa el pago seguro para confirmar el pedido y pasar a coordinación de entrega.",
    };
  }

  const best = matches[0];
  if (best && (!purchaseIntent || !genericPurchaseIntent || !memory.selectedProductId)) {
    memory = {
      ...memory,
      stage: memory.stage ?? "idle",
      selectedProductId: best.product.id,
      selectedProductName: best.product.name,
      selectedSku: best.product.sku,
      selectedPriceUsd: best.product.priceUsd,
      selectedStock: best.product.stock,
      quantity: parseQuantity(message),
    };
  }

  if (purchaseIntent && genericPurchaseIntent && memory.selectedProductId && memory.selectedProductName) {
    const quantity = parseQuantity(message);
    memory = { ...memory, quantity };
    if ((memory.selectedStock || 0) < quantity) {
      return {
        handled: true,
        memory,
        reply: `Tengo disponibilidad limitada de ${memory.selectedProductName}: quedan ${memory.selectedStock || 0}. Puedo ajustar la cantidad o buscar una alternativa compatible.`,
      };
    }
    memory = { ...memory, stage: "contact" };
    return {
      handled: true,
      memory,
      reply: `Perfecto, avanzamos con ${memory.selectedProductName} (${memory.selectedSku}). Confirmo ${memory.selectedStock} disponible(s), precio $${memory.selectedPriceUsd}. Para cerrar la venta, envíame tu nombre y teléfono.`,
    };
  }

  if (purchaseIntent && best) {
    if (best.product.stock < (memory.quantity || 1)) {
      return {
        handled: true,
        memory,
        reply: `Tengo disponibilidad limitada de ${best.product.name}: quedan ${best.product.stock}. Puedo ajustar la cantidad o buscar una alternativa compatible.`,
      };
    }
    memory = { ...memory, stage: "contact" };
    return {
      handled: true,
      memory,
      reply: `La opción recomendada es ${best.product.name} (${best.product.sku}). Confirmo ${best.product.stock} disponible(s), precio $${best.product.priceUsd}. Para cerrar la venta, envíame tu nombre y teléfono.`,
    };
  }

  return { handled: false, memory };
}

export function buildSalesPush(memory: SalesMemory) {
  if (!memory.selectedProductId || !memory.selectedStock || memory.selectedStock <= 0) {
    return "";
  }
  return `\n\nTengo ${memory.selectedStock} disponible(s) de ${memory.selectedProductName} por $${memory.selectedPriceUsd}. Si quieres asegurar esta pieza ahora, responde "comprar" y te llevo al pago.`;
}

async function createCheckoutFromMemory(input: {
  businessId: string;
  conversationId: string;
  memory: SalesMemory;
}) {
  if (!input.memory.selectedProductId) {
    throw new Error("No hay producto seleccionado para pagar.");
  }

  const product = await db.query.products.findFirst({
    where: and(eq(products.id, input.memory.selectedProductId), eq(products.businessId, input.businessId)),
  });
  if (!product) throw new Error("Producto no encontrado.");

  const quantity = Math.max(1, input.memory.quantity || 1);
  if (product.stock < quantity) throw new Error("No hay stock suficiente para completar la compra.");
  const amountUsd = (Number(product.priceUsd) * quantity).toFixed(2);

  const [checkout] = await db
    .insert(checkoutSessions)
    .values({
      businessId: input.businessId,
      conversationId: input.conversationId,
      productId: product.id,
      quantity,
      status: "requires_payment_method",
      customerName: input.memory.customerName,
      customerPhone: input.memory.customerPhone,
      deliveryAddress: input.memory.deliveryAddress,
      amountUsd,
      expiresAt: sql`now() + interval '30 minutes'`,
      metadata: {
        productName: product.name,
        sku: product.sku,
      },
    })
    .returning();

  return {
    id: checkout.id,
    status: checkout.status,
    amountUsd: checkout.amountUsd,
    productName: product.name,
    quantity,
  };
}

export async function getCheckoutSummary(checkoutSessionId: string) {
  const checkout = await db.query.checkoutSessions.findFirst({
    where: eq(checkoutSessions.id, checkoutSessionId),
    with: { product: true },
  });
  if (!checkout) throw new Error("Sesión de pago no encontrada.");
  return {
    id: checkout.id,
    status: checkout.status,
    amountUsd: checkout.amountUsd,
    productName: checkout.product.name,
    quantity: checkout.quantity,
  };
}

async function buildOrderStatusReply(memory: SalesMemory) {
  if (!memory.lastOrderId) {
    return "Todavía no tengo un pedido confirmado en esta conversación.";
  }

  const order = await db.query.orders.findFirst({
    where: eq(orders.id, memory.lastOrderId),
    with: {
      items: {
        with: { product: true },
      },
    },
  });

  if (!order) {
    return `Tengo registrado el pedido #${memory.lastOrderId.slice(0, 8)}, pero no pude cargar el detalle en este momento. Referencia de pago: ${memory.lastPaymentReference || "registrada"}.`;
  }

  const items = order.items
    .map((item) => `${item.quantity} x ${item.product.name}`)
    .join(", ");
  const deliveryLabel = formatDeliveryStatus(order.deliveryStatus);

  return `Tu pedido #${order.id.slice(0, 8)} está ${formatOrderStatus(order.status)}. Compraste: ${items}. Total: $${order.totalUsd}. Pago: ${order.paymentStatus === "paid" ? `aprobado (${order.paymentReference || memory.lastPaymentReference || "referencia registrada"})` : order.paymentStatus}. Entrega: ${deliveryLabel}${order.deliveryAddress ? `, ${order.deliveryAddress}` : ""}. La tienda te contactará por ${order.customerPhone || memory.customerPhone || "el teléfono indicado"}.`;
}

function formatOrderStatus(status: string) {
  const labels: Record<string, string> = {
    draft: "en borrador",
    quote_requested: "pendiente de cotización",
    pending: "pendiente",
    confirmed: "confirmado",
    ready: "listo",
    delivered: "entregado",
    cancelled: "cancelado",
  };
  return labels[status] || status;
}

function formatDeliveryStatus(status: string) {
  const labels: Record<string, string> = {
    pending_coordination: "pendiente de coordinación",
    coordinated: "coordinada",
    on_the_way: "en camino",
    delivered: "entregada",
  };
  return labels[status] || status;
}
