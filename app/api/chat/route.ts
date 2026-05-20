import { NextResponse } from "next/server";
import { z } from "zod";
import { aiProvider } from "@/lib/ai/provider";
import { getDefaultBusiness } from "@/lib/db/defaults";
import { appendConversationMessage, getConversationMemory, updateConversationMemory } from "@/lib/services/conversations";
import { listAvailableProducts } from "@/lib/services/inventory";
import { missingVehicleFields, scoreProducts } from "@/lib/services/matching";
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
      recentSummary: z.string().optional(),
      checkoutStage: z.string().optional(),
      selectedProduct: z.unknown().optional(),
      lastOrderId: z.string().optional(),
      lastPaymentReference: z.string().optional(),
      customerPhone: z.string().optional(),
      deliveryAddress: z.string().optional(),
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

export async function POST(request: Request) {
  try {
    const json = await request.json();
    const parsed = chatSchema.parse(json);
    const business = await getDefaultBusiness();

    const vehicle = extractVehicle(parsed.message, parsed.vehicle);
    const inventory = await listAvailableProducts(business.id);
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
        recentSummary: parsed.memory?.recentSummary,
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
