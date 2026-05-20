import { GoogleGenerativeAI } from "@google/generative-ai";
import type { VehicleInfo } from "@/lib/db/schema";
import type { ProductMatch } from "@/lib/services/matching";
import { missingVehicleFields } from "@/lib/services/matching";

export type ChatReplyInput = {
  message: string;
  vehicle: VehicleInfo;
  matches: ProductMatch[];
  missingFields: string[];
  image?: {
    mimeType: string;
    data: string;
  };
  memory?: {
    recentSummary?: string;
    checkoutStage?: string;
    selectedProduct?: unknown;
    lastOrderId?: string;
    lastPaymentReference?: string;
    customerPhone?: string;
    deliveryAddress?: string;
  };
};

export type AiProvider = {
  generateReply(input: ChatReplyInput): Promise<string>;
};

class GeminiProvider implements AiProvider {
  private modelName = process.env.GEMINI_MODEL || "gemini-2.5-flash";

  async generateReply(input: ChatReplyInput) {
    const strongMatch = input.matches[0];
    if (!input.image && input.vehicle.make && input.vehicle.model && !strongMatch) {
      return deterministicReply(input);
    }
    if (!input.image && strongMatch && strongMatch.confidence >= 0.55 && strongMatch.product.stock > 0) {
      return deterministicReply(input);
    }

    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      return deterministicReply(input);
    }

    const topMatches = input.matches.map((match) => ({
      producto: match.product.name,
      sku: match.product.sku,
      precioUsd: match.product.priceUsd,
      stock: match.product.stock,
      confianza: match.confidence,
      razones: match.reasons,
    }));
    const text =
      "Eres ReplecIA, asesor IA para una tienda de autopartes. Responde en español, breve, profesional y útil. " +
      "No inventes disponibilidad ni compatibilidad. Si faltan datos del vehículo, pídelos. " +
      "Si el usuario adjunta una imagen de un vehículo, primero intenta inferir marca, modelo o generación aproximada a partir de rasgos visibles. " +
      "Si adjunta una imagen de una pieza, intenta identificar el tipo de pieza visible (por ejemplo alternador, radiador, compresor, filtro, pastilla, amortiguador) y explica qué datos necesita para vender la correcta. " +
      "Formula siempre la inferencia como hipótesis: 'Por la imagen parece...' o 'Podría ser...'. " +
      "Luego pide confirmación de vehículo o código/medida de pieza. No digas simplemente que no puedes verlo si hay rasgos visuales útiles. " +
      "Usa la imagen solo como apoyo, no como certeza absoluta. " +
      "Si hay coincidencias, recomienda máximo 2 repuestos y explica que la tienda debe confirmar antes de cerrar la venta.\n\n" +
      `Mensaje: ${input.message}\n` +
      `Vehículo: ${JSON.stringify(input.vehicle)}\n` +
      `Memoria reciente: ${input.memory?.recentSummary || "sin historial"}\n` +
      `Estado comercial: ${JSON.stringify(input.memory || {})}\n` +
      `Datos faltantes: ${input.missingFields.join(", ") || "ninguno"}\n` +
      `Coincidencias: ${JSON.stringify(topMatches)}`;
    const parts = input.image
      ? [
          { text },
          {
            inlineData: {
              mimeType: input.image.mimeType,
              data: input.image.data,
            },
          },
        ]
      : [{ text }];

    const genAI = new GoogleGenerativeAI(apiKey);
    const modelNames = [...new Set([this.modelName, "gemini-2.0-flash", "gemini-2.0-flash-lite"])];
    let lastError: unknown = null;

    for (const modelName of modelNames) {
      try {
        const model = genAI.getGenerativeModel({ model: modelName });
        const result = await model.generateContent({
        contents: [
          {
            role: "user",
            parts,
          },
        ],
        generationConfig: {
          temperature: 0.4,
          topP: 0.9,
          maxOutputTokens: 220,
        },
      });

        const reply = (result.response.text() || "").trim();
        if (isIncompleteReply(reply, Boolean(input.image))) {
          return deterministicReply(input);
        }
        return input.image ? enrichVisionReply(reply, input) : reply;
      } catch (error) {
        lastError = error;
        console.warn("gemini_provider_fallback", modelName, error);
      }
    }

    if (input.image && isQuotaError(lastError)) {
      return visionUnavailableReply(input);
    }
    return deterministicReply(input);
  }
}

export const aiProvider: AiProvider = new GeminiProvider();

export function deterministicReply(input: ChatReplyInput) {
  const normalized = input.message
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");

  if (input.image) {
    const missing = input.missingFields.length ? input.missingFields : missingVehicleFields(input.vehicle);
    if (missing.length > 0) {
      return `Recibí la foto. Puedo tomarla como referencia visual para identificar si es una pieza o un vehículo, pero necesito que confirmes ${missing.join(", ")} del vehículo, o el código/medida de la pieza, para evitar venderte algo incompatible.`;
    }
    return "Recibí la foto. Puedo usarla como apoyo, pero la compatibilidad debe confirmarse con los datos del vehículo y el código o medida de la pieza.";
  }

  if (/^(hola+|buenas|buenos dias|buenas tardes|buenas noches|saludos)\b/.test(normalized)) {
    return "¡Hola! Claro, puedo ayudarte a encontrar el repuesto correcto. Dime qué falla presenta el vehículo o qué pieza necesitas, junto con marca, modelo y año.";
  }

  if (/ayuda|ayudar|que puedes hacer|como funciona/.test(normalized)) {
    return "Puedo orientarte con diagnóstico básico, revisar compatibilidad por marca, modelo y año, verificar stock disponible y dejar una solicitud de pedido para que la tienda la confirme.";
  }

  const missing = input.missingFields.length ? input.missingFields : missingVehicleFields(input.vehicle);
  if (missing.length > 0) {
    const hasMakeModel = Boolean(input.vehicle.make && input.vehicle.model);
    if (hasMakeModel && !input.matches[0]) {
      return `Tengo ${input.vehicle.make} ${input.vehicle.model}${input.vehicle.year ? ` ${input.vehicle.year}` : ""} como referencia, pero necesito saber qué pieza específica te pidió el mecánico o ver el código/medida de la pieza. No quiero recomendarte un repuesto incompatible.`;
    }
    return `Para verificar compatibilidad necesito ${missing.join(", ")} del vehículo. Si conoces el motor, también ayuda a evitar errores.`;
  }

  const best = input.matches[0];
  if (!best || best.confidence < 0.28) {
    if (input.vehicle.make && input.vehicle.model) {
      return `Tengo como referencia un ${input.vehicle.make} ${input.vehicle.model}${input.vehicle.year ? ` ${input.vehicle.year}` : ""}, pero no tengo una coincidencia compatible en inventario con la pieza solicitada. Envíame una foto más cercana de la pieza, el código, medida o nombre que te dio el mecánico para orientarte sin inventar un repuesto.`;
    }
    return "No tengo suficiente certeza para recomendar una pieza exacta. Te puedo dejar la solicitud para revisión manual con un asesor de la tienda.";
  }

  const second = input.matches[1];
  const extra = second ? ` También podría revisar ${second.product.name}.` : "";
  return `La opción más probable es ${best.product.name} (${best.product.sku}), con stock disponible y precio referencial de $${best.product.priceUsd}.${extra} Recomiendo confirmar compatibilidad antes de cerrar la venta.`;
}

export function isIncompleteReply(reply: string, isVision = false) {
  if (!reply) return true;
  if (isVision && mentionsKnownVehiclePart(reply)) return false;
  if (reply.length < 45) return true;
  if (/[,:;]$/.test(reply.trim())) return true;
  if (/\b(tu|su|del|de la|para|con|por)$/i.test(reply.trim())) return true;
  if (/marca,\s*modelo\s*y\s*a[oñ]o/i.test(reply) && /Tengo \d+ disponible/i.test(reply)) return true;
  const words = reply.split(/\s+/).filter(Boolean);
  if (words.length < 8) return true;
  return false;
}

function mentionsKnownVehiclePart(reply: string) {
  return /\b(alternador|radiador|compresor|filtro|pastilla|amortiguador|sensor|buj[ií]a|correa|bomba|inyector|arranque|motor de arranque|disco de freno)\b/i.test(reply);
}

export function enrichVisionReply(reply: string, input: ChatReplyInput) {
  const trimmed = reply.trim();
  const needsVehicle = missingVehicleFields(input.vehicle);
  if (mentionsKnownVehiclePart(trimmed) && needsVehicle.length > 0) {
    return `${trimmed}\n\nPara venderte la pieza correcta necesito confirmar ${needsVehicle.join(", ")} del vehículo. Si tienes el código o número de parte visible, envíamelo también.`;
  }
  return trimmed;
}

export function visionUnavailableReply(input: ChatReplyInput) {
  const missing = input.missingFields.length ? input.missingFields : missingVehicleFields(input.vehicle);
  return `No pude analizar visualmente la imagen en este momento porque el servicio de visión IA alcanzó su límite temporal. Para orientarte sin inventar, dime qué rasgos se ven en la pieza (polea, conector, mangueras, código, medida o etiqueta) y confirma ${missing.join(", ")} del vehículo.`;
}

function isQuotaError(error: unknown) {
  if (!error || typeof error !== "object") return false;
  const maybe = error as { status?: number; message?: string; statusText?: string };
  return maybe.status === 429 || /quota|too many requests|rate/i.test(`${maybe.message || ""} ${maybe.statusText || ""}`);
}
