import { and, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { checkoutSessions, products, type SalesMemory } from "@/lib/db/schema";
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

export function isStatusIntent(message: string) {
  return /pedido|orden|compra|entrega|delivery|pago|estado|como va|seguimiento/i.test(message);
}

export function isCancelIntent(message: string) {
  return /cancel|anular|olvida|no quiero|mejor no/i.test(message);
}

export function isVisualIdentificationIntent(message: string) {
  return /imagen|foto|ves|ver|reconoc|marca|modelo|que carro|que auto|no se cual/i.test(message);
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

  if (isVisualIdentificationIntent(message) && !isPurchaseIntent(message)) {
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

  if (isStatusIntent(message) && memory.lastOrderId) {
    return {
      handled: true,
      memory,
      reply: `Tu pedido #${memory.lastOrderId.slice(0, 8)} está confirmado y en coordinación de entrega. Pago aprobado con referencia ${memory.lastPaymentReference || "registrada"}. La tienda te contactará por ${memory.customerPhone || "el teléfono indicado"}.`,
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
  if (best) {
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

  if (isPurchaseIntent(message) && best) {
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
