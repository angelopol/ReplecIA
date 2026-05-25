import { describe, expect, it } from "vitest";
import { deterministicReply, enrichVisionReply, isIncompleteReply, visionUnavailableReply } from "@/lib/ai/provider";

describe("vision replies", () => {
  it("accepts short part-identification replies", () => {
    expect(isIncompleteReply("Parece un alternador.", true)).toBe(false);
  });

  it("adds compatibility guidance to visual part identification", () => {
    const reply = enrichVisionReply("Por la imagen parece un alternador.", {
      message: "que pieza es?",
      vehicle: {},
      matches: [],
      missingFields: ["marca", "modelo", "año"],
      image: { mimeType: "image/jpeg", data: "abc" },
    });

    expect(reply).toContain("alternador");
    expect(reply).toContain("marca");
    expect(reply).toContain("código");
  });

  it("explains when vision quota is unavailable instead of using generic fallback", () => {
    const reply = visionUnavailableReply({
      message: "que pieza es?",
      vehicle: {},
      matches: [],
      missingFields: ["marca", "modelo", "año"],
      image: { mimeType: "image/jpeg", data: "abc" },
    });

    expect(reply).toContain("No pude analizar visualmente");
    expect(reply).toContain("límite");
    expect(reply).toContain("polea");
  });
});

describe("inventory replies", () => {
  it("answers catalog questions with inventory instead of compatibility fallback", () => {
    const reply = deterministicReply({
      message: "cual es tu inventario para saber que pedirte",
      vehicle: { make: "toyota", model: "corolla", year: 2021 },
      matches: [],
      missingFields: [],
      inventoryOverview:
        "Familias disponibles: frenos (2), electrico (3).\nEjemplos:\n- Alternador Toyota Corolla 2009-2013 (ALT-COR-09-13): $145.00, stock 4",
    });

    expect(reply).toContain("inventario activo");
    expect(reply).toContain("Alternador Toyota Corolla");
    expect(reply).not.toContain("no tengo una coincidencia compatible");
  });
});
