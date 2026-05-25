import { NextResponse } from "next/server";
import { z } from "zod";
import { aiProvider } from "@/lib/ai/provider";
import { getDefaultBusiness } from "@/lib/db/defaults";
import { appendConversationMessage, getConversationMemory, updateConversationMemory } from "@/lib/services/conversations";
import { listAvailableProducts } from "@/lib/services/inventory";
import { missingVehicleFields, scoreProducts } from "@/lib/services/matching";
import type { InventoryProduct } from "@/lib/services/matching";
import { buildSalesPush, handleCommercialTurn } from "@/lib/services/sales";
import type { VehicleInfo } from "@/lib/db/schema";

const chatSchema = z.object({
  conversationId: z.string().uuid().optional(),
  message: z.string().trim().min(1).max(1200),
  image: z
    .object({
      mimeType: z.string().regex(/^image\/(png|jpe?g|webp|gif)$/),
      data: z.string().min(1).max(6_000_000),
    })
    .optional(),
  memory: z
    .object({
      stage: z.enum(["idle", "contact", "delivery", "payment", "completed"]).optional(),
      recentSummary: z.string().optional(),
      checkoutStage: z.string().optional(),
      selectedProductId: z.string().optional(),
      selectedProductName: z.string().optional(),
      selectedSku: z.string().optional(),
      selectedPriceUsd: z.string().optional(),
      selectedStock: z.number().optional(),
      quantity: z.number().optional(),
      selectedProduct: z.unknown().optional(),
      lastOrderId: z.string().optional(),
      lastPaymentReference: z.string().optional(),
      customerName: z.string().optional(),
      customerPhone: z.string().optional(),
      deliveryAddress: z.string().optional(),
      checkoutSessionId: z.string().optional(),
    })
    .optional(),
  vehicle: z
    .object({
      make: z.string().optional(),
      model: z.string().optional(),
      year: z.coerce.number().int().optional(),
      engine: z.string().optional(),
    })
    .optional()
    .default({}),
});

function extractVehicle(message: string, current: VehicleInfo): VehicleInfo {
  const next = { ...current };
  const year = message.match(/\b(19[7-9]\d|20[0-3]\d)\b/);
  if (year) next.year = Number(year[1]);

  const lower = message.toLowerCase();
  const knownMakes = ["toyota", "chevrolet", "ford", "hyundai", "kia", "nissan", "mazda", "honda", "jeep"];
  const foundMake = knownMakes.find((make) => lower.includes(make));
  if (foundMake) next.make = foundMake;

  const modelMatch = message.match(/\b(corolla|yaris|hilux|aveo|optra|fiesta|explorer|accent|rio|sentra|civic|cherokee|elantra)\b/i);
  if (modelMatch) next.model = modelMatch[1];
  if (!next.make && next.model) {
    const modelMakeMap: Record<string, string> = {
      corolla: "toyota",
      yaris: "toyota",
      hilux: "toyota",
      aveo: "chevrolet",
      optra: "chevrolet",
      fiesta: "ford",
      explorer: "ford",
      accent: "hyundai",
      elantra: "hyundai",
      rio: "kia",
      sentra: "nissan",
      civic: "honda",
      cherokee: "jeep",
    };
    next.make = modelMakeMap[String(next.model).toLowerCase()] || next.make;
  }

  const engineMatch = message.match(/\b(\d\.\d\s?(?:l|lts|litros)?|diesel|gasolina)\b/i);
  if (engineMatch) next.engine = engineMatch[1];

  return next;
}

function buildInventoryOverview(products: InventoryProduct[]) {
  const active = products.filter((product) => product.stock > 0);
  const families = new Map<string, number>();
  const examples = active.slice(0, 10).map((product) => {
    const vehicles = (product.compatibility || [])
      .slice(0, 2)
      .map((item) => `${item.make} ${item.model}${item.yearFrom || item.yearTo ? ` ${item.yearFrom || ""}-${item.yearTo || ""}` : ""}`)
      .join(", ");
    const family = inferInventoryFamily(product.name, product.description);
    families.set(family, (families.get(family) || 0) + 1);
    return `- ${product.name} (${product.sku}): $${product.priceUsd}, stock ${product.stock}${vehicles ? `, compatible con ${vehicles}` : ""}`;
  });

  for (const product of active.slice(10)) {
    const family = inferInventoryFamily(product.name, product.description);
    families.set(family, (families.get(family) || 0) + 1);
  }

  const familySummary = [...families.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([family, count]) => `${family} (${count})`)
    .join(", ");

  return [`Familias disponibles: ${familySummary || "sin productos activos"}.`, `Ejemplos:`, ...examples].join("\n");
}

function inferInventoryFamily(name: string, description: string) {
  const text = `${name} ${description}`
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");

  if (/freno|pastilla|disco/.test(text)) return "frenos";
  if (/alternador|arranque|bateria|electr/.test(text)) return "eléctrico";
  if (/radiador|termostato|agua|enfriamiento/.test(text)) return "enfriamiento";
  if (/filtro|aceite/.test(text)) return "filtros";
  if (/amortiguador|suspension|base/.test(text)) return "suspensión";
  if (/bujia|bobina|inyector|motor/.test(text)) return "motor";
  if (/sensor|oxigeno|cigue/.test(text)) return "sensores";
  return "otros";
}

export async function POST(request: Request) {
  try {
    const json = await request.json();
    const parsed = chatSchema.parse(json);
    const business = await getDefaultBusiness();

    const vehicle = extractVehicle(parsed.message, parsed.vehicle);
    const inventory = await listAvailableProducts(business.id);
    const inventoryOverview = buildInventoryOverview(inventory);
    const shouldMatchInventoryFromText = !parsed.image || /\b(compr|precio|tienen|disponible|necesito|busco|quiero)\b/i.test(parsed.message);
    const matches = shouldMatchInventoryFromText && !parsed.image ? scoreProducts(parsed.message, vehicle, inventory) : [];
    const missingFields = missingVehicleFields(vehicle);
    const confidence = matches[0]?.confidence ?? 0;

    const conversationId = await appendConversationMessage({
      businessId: business.id,
      conversationId: parsed.conversationId,
      role: "user",
      content: parsed.message,
    });
    const persistedMemory = parsed.conversationId ? await getConversationMemory(conversationId) : {};
    const commercial = await handleCommercialTurn({
      businessId: business.id,
      conversationId,
      message: parsed.message,
      memory: {
        ...persistedMemory,
        ...parsed.memory,
        recentSummary: parsed.memory?.recentSummary || persistedMemory.recentSummary,
      },
      matches,
    });

    if (commercial.handled && commercial.reply) {
      await appendConversationMessage({
        businessId: business.id,
        conversationId,
        role: "assistant",
        content: commercial.reply,
        vehicle,
      });
      await updateConversationMemory(conversationId, commercial.memory);
      return NextResponse.json({
        conversationId,
        reply: commercial.reply,
        vehicle,
        matches: matches.map((match) => ({
          productId: match.product.id,
          name: match.product.name,
          sku: match.product.sku,
          priceUsd: match.product.priceUsd,
          stock: match.product.stock,
          confidence: match.confidence,
        })),
        commercialState: commercial.memory,
        checkout: commercial.checkout,
      });
    }

    const reply = await aiProvider.generateReply({
      message: parsed.message,
      vehicle,
      matches,
      missingFields,
      image: parsed.image,
      memory: parsed.memory,
      inventoryOverview,
    });
    const canPushSale = !parsed.image && (matches[0]?.confidence ?? 0) >= 0.55 && matches[0]?.product.stock > 0;
    const finalReply = `${reply}${canPushSale ? buildSalesPush(commercial.memory) : ""}`;

    await appendConversationMessage({
      businessId: business.id,
      conversationId,
      role: "assistant",
      content: finalReply,
      vehicle,
      diagnosis: {
        symptom: parsed.message,
        requestedPart: matches[0]?.product.name,
        confidence,
        recommendation: matches[0]?.product.name,
        missingFields,
      },
    });
    await updateConversationMemory(conversationId, commercial.memory);

    return NextResponse.json({
      conversationId,
      reply: finalReply,
      vehicle,
      matches: matches.map((match) => ({
        productId: match.product.id,
        name: match.product.name,
        sku: match.product.sku,
        priceUsd: match.product.priceUsd,
        stock: match.product.stock,
        confidence: match.confidence,
      })),
      commercialState: commercial.memory,
    });
  } catch (error) {
    console.error("chat_api_error", error);
    return NextResponse.json(
      {
        error: "chat_failed",
        reply:
          "Ahora mismo no pude completar la consulta. Intenta de nuevo en unos segundos o deja la solicitud para revisión manual.",
      },
      { status: 500 },
    );
  }
}
