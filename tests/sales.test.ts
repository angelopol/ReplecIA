import { describe, expect, it } from "vitest";
import {
  handleCommercialTurn,
  isGenericPurchaseIntent,
  isPurchaseIntent,
  isStatusIntent,
  parseContact,
  parseQuantity,
} from "@/lib/services/sales";
import type { ProductMatch } from "@/lib/services/matching";

describe("sales helpers", () => {
  it("detects purchase intent", () => {
    expect(isPurchaseIntent("quiero comprar las pastillas")).toBe(true);
    expect(isPurchaseIntent("me lo llevo")).toBe(true);
    expect(isGenericPurchaseIntent("comprar")).toBe(true);
    expect(isGenericPurchaseIntent("comprar 2 unidades")).toBe(true);
    expect(isGenericPurchaseIntent("quiero comprar radiador")).toBe(false);
  });

  it("detects order status intent", () => {
    expect(isStatusIntent("como va mi pedido?")).toBe(true);
    expect(isStatusIntent("estado de entrega")).toBe(true);
  });

  it("parses contact and quantity", () => {
    expect(parseContact("Angel 04124856320")).toEqual({
      customerName: "Angel",
      customerPhone: "04124856320",
    });
    expect(parseQuantity("quiero 2 unidades")).toBe(2);
  });

  it("keeps the selected product when the user confirms with a generic comprar", async () => {
    const radiadorMatch: ProductMatch = {
      product: {
        id: "radiador",
        name: "Radiador Chevrolet Aveo 2007-2018",
        sku: "RAD-AVE-07-18",
        brand: "Koyorad",
        description: "Radiador de aluminio para sistema de enfriamiento.",
        priceUsd: "92.00",
        stock: 7,
      },
      confidence: 0.5,
      reasons: ["compatibilidad vehicular registrada", "hay stock disponible"],
    };

    const result = await handleCommercialTurn({
      businessId: "business",
      conversationId: "conversation",
      message: "comprar",
      matches: [radiadorMatch],
      memory: {
        stage: "idle",
        selectedProductId: "alternador",
        selectedProductName: "Alternador Chevrolet Aveo 2007-2018",
        selectedSku: "ALT-AVE-07-18",
        selectedPriceUsd: "112.00",
        selectedStock: 6,
        quantity: 1,
      },
    });

    expect(result.handled).toBe(true);
    expect(result.memory.selectedProductId).toBe("alternador");
    expect(result.reply).toContain("Alternador Chevrolet Aveo");
    expect(result.reply).not.toContain("Radiador Chevrolet Aveo");
  });
});
