import { describe, expect, it } from "vitest";
import { enrichVisionReply, isIncompleteReply, visionUnavailableReply } from "@/lib/ai/provider";

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
