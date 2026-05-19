import { GoogleGenerativeAI } from "@google/generative-ai";
import type { VehicleInfo } from "@/lib/db/schema";
import type { ProductMatch } from "@/lib/services/matching";
import { missingVehicleFields } from "@/lib/services/matching";

export type ChatReplyInput = {
  message: string;
  vehicle: VehicleInfo;
  matches: ProductMatch[];
  missingFields: string[];
};

export type AiProvider = {
  generateReply(input: ChatReplyInput): Promise<string>;
};

class GeminiProvider implements AiProvider {
  private modelName = process.env.GEMINI_MODEL || "gemini-1.5-flash";

  async generateReply(input: ChatReplyInput) {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      return deterministicReply(input);
    }

    const genAI = new GoogleGenerativeAI(apiKey);
    const model = genAI.getGenerativeModel({ model: this.modelName });
    const topMatches = input.matches.map((match) => ({
      producto: match.product.name,
      sku: match.product.sku,
      precioUsd: match.product.priceUsd,
      stock: match.product.stock,
      confianza: match.confidence,
      razones: match.reasons,
    }));

    const result = await model.generateContent({
      contents: [
        {
          role: "user",
          parts: [
            {
              text:
                "Eres ReplecIA, asesor IA para una tienda de autopartes. Responde en español, breve, profesional y útil. " +
                "No inventes disponibilidad ni compatibilidad. Si faltan datos del vehículo, pídelos. " +
                "Si hay coincidencias, recomienda máximo 2 repuestos y explica que la tienda debe confirmar antes de cerrar la venta.\n\n" +
                `Mensaje: ${input.message}\n` +
                `Vehículo: ${JSON.stringify(input.vehicle)}\n` +
                `Datos faltantes: ${input.missingFields.join(", ") || "ninguno"}\n` +
                `Coincidencias: ${JSON.stringify(topMatches)}`,
            },
          ],
        },
      ],
      generationConfig: {
        temperature: 0.4,
        topP: 0.9,
        maxOutputTokens: 220,
      },
    });

    return result.response.text() || deterministicReply(input);
  }
}

export const aiProvider: AiProvider = new GeminiProvider();

export function deterministicReply(input: ChatReplyInput) {
  const missing = input.missingFields.length ? input.missingFields : missingVehicleFields(input.vehicle);
  if (missing.length > 0) {
    return `Para verificar compatibilidad necesito ${missing.join(", ")} del vehículo. Si conoces el motor, también ayuda a evitar errores.`;
  }

  const best = input.matches[0];
  if (!best || best.confidence < 0.28) {
    return "No tengo suficiente certeza para recomendar una pieza exacta. Te puedo dejar la solicitud para revisión manual con un asesor de la tienda.";
  }

  const second = input.matches[1];
  const extra = second ? ` También podría revisar ${second.product.name}.` : "";
  return `La opción más probable es ${best.product.name} (${best.product.sku}), con stock disponible y precio referencial de $${best.product.priceUsd}.${extra} Recomiendo confirmar compatibilidad antes de cerrar la venta.`;
}
