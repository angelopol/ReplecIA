import { describe, expect, it } from "vitest";
import { isPurchaseIntent, isStatusIntent, parseContact, parseQuantity } from "@/lib/services/sales";

describe("sales helpers", () => {
  it("detects purchase intent", () => {
    expect(isPurchaseIntent("quiero comprar las pastillas")).toBe(true);
    expect(isPurchaseIntent("me lo llevo")).toBe(true);
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
});
