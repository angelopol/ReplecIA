import { describe, expect, it } from "vitest";
import { missingVehicleFields, scoreProducts } from "@/lib/services/matching";

const products = [
  {
    id: "1",
    name: "Pastillas de freno delanteras Toyota Corolla",
    sku: "BRK-COR",
    brand: "Akebono",
    description: "Pastillas cerámicas",
    priceUsd: "38.00",
    stock: 4,
    compatibility: [{ make: "Toyota", model: "Corolla", yearFrom: 2009, yearTo: 2013, engine: "1.8" }],
  },
  {
    id: "2",
    name: "Radiador Chevrolet Aveo",
    sku: "RAD-AVE",
    brand: "GM",
    description: "Sistema de enfriamiento",
    priceUsd: "92.00",
    stock: 0,
    compatibility: [{ make: "Chevrolet", model: "Aveo", yearFrom: 2007, yearTo: 2018, engine: null }],
  },
];

describe("matching", () => {
  it("asks for vehicle fields when they are missing", () => {
    expect(missingVehicleFields({ make: "Toyota" })).toEqual(["modelo", "año"]);
  });

  it("scores compatible stocked products higher", () => {
    const matches = scoreProducts("mi corolla no frena bien", { make: "Toyota", model: "Corolla", year: 2012 }, products);
    expect(matches[0].product.id).toBe("1");
    expect(matches[0].confidence).toBeGreaterThan(0.4);
  });

  it("does not recommend products without stock", () => {
    const matches = scoreProducts("se calienta el aveo", { make: "Chevrolet", model: "Aveo", year: 2014 }, products);
    expect(matches.find((match) => match.product.id === "2")).toBeUndefined();
  });
});
