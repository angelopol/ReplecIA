import { NextResponse } from "next/server";
import { z } from "zod";
import { aiProvider } from "@/lib/ai/provider";
import { getDefaultBusiness } from "@/lib/db/defaults";
import { appendConversationMessage } from "@/lib/services/conversations";
import { listAvailableProducts } from "@/lib/services/inventory";
import { missingVehicleFields, scoreProducts } from "@/lib/services/matching";
import type { VehicleInfo } from "@/lib/db/schema";

const chatSchema = z.object({
  conversationId: z.string().uuid().optional(),
  message: z.string().trim().min(1).max(1200),
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

  const modelMatch = message.match(/\b(corolla|yaris|hilux|aveo|optra|fiesta|explorer|accent|rio|sentra|civic|cherokee)\b/i);
  if (modelMatch) next.model = modelMatch[1];

  const engineMatch = message.match(/\b(\d\.\d\s?(?:l|lts|litros)?|diesel|gasolina)\b/i);
  if (engineMatch) next.engine = engineMatch[1];

  return next;
}

export async function POST(request: Request) {
  const json = await request.json();
  const parsed = chatSchema.parse(json);
  const business = await getDefaultBusiness();
  const vehicle = extractVehicle(parsed.message, parsed.vehicle);
  const inventory = await listAvailableProducts(business.id);
  const matches = scoreProducts(parsed.message, vehicle, inventory);
  const missingFields = missingVehicleFields(vehicle);
  const confidence = matches[0]?.confidence ?? 0;

  const conversationId = await appendConversationMessage({
    businessId: business.id,
    conversationId: parsed.conversationId,
    role: "user",
    content: parsed.message,
  });

  const reply = await aiProvider.generateReply({
    message: parsed.message,
    vehicle,
    matches,
    missingFields,
  });

  await appendConversationMessage({
    businessId: business.id,
    conversationId,
    role: "assistant",
    content: reply,
    vehicle,
    diagnosis: {
      symptom: parsed.message,
      requestedPart: matches[0]?.product.name,
      confidence,
      recommendation: matches[0]?.product.name,
      missingFields,
    },
  });

  return NextResponse.json({
    conversationId,
    reply,
    vehicle,
    matches: matches.map((match) => ({
      productId: match.product.id,
      name: match.product.name,
      sku: match.product.sku,
      priceUsd: match.product.priceUsd,
      confidence: match.confidence,
    })),
  });
}
