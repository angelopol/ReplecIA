import type { VehicleInfo } from "@/lib/db/schema";

export type InventoryProduct = {
  id: string;
  name: string;
  sku: string;
  brand: string | null;
  description: string;
  priceUsd: string;
  stock: number;
  compatibility?: {
    make: string;
    model: string;
    yearFrom: number | null;
    yearTo: number | null;
    engine: string | null;
  }[];
};

export type ProductMatch = {
  product: InventoryProduct;
  confidence: number;
  reasons: string[];
};

const symptomPartMap: Array<[RegExp, string[]]> = [
  [/fren|chill(a|i)|rueda|pastilla|disco/i, ["freno", "pastilla", "disco"]],
  [/calient|temperatura|radiador|refrigerante|agua/i, ["radiador", "termostato", "bomba de agua"]],
  [/no prende|arranc|bateria|alternador|corriente/i, ["bateria", "alternador", "arranque"]],
  [/aceite|lubric|filtro/i, ["aceite", "filtro"]],
  [/amortigu|suspension|golpe/i, ["amortiguador", "suspension", "base"]],
  [/buj[ií]a|chispa|falla|tiembla/i, ["bujia", "bobina", "inyector"]],
];

export function normalizeSearchText(value: string) {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function inferPartTerms(message: string) {
  const normalized = normalizeSearchText(message);
  const terms = new Set(normalized.split(" ").filter((word) => word.length > 3));

  for (const [pattern, mapped] of symptomPartMap) {
    if (pattern.test(message)) {
      mapped.forEach((term) => terms.add(term));
    }
  }

  return [...terms];
}

export function missingVehicleFields(vehicle: VehicleInfo) {
  const missing: string[] = [];
  if (!vehicle.make) missing.push("marca");
  if (!vehicle.model) missing.push("modelo");
  if (!vehicle.year) missing.push("año");
  return missing;
}

export function scoreProducts(message: string, vehicle: VehicleInfo, products: InventoryProduct[]): ProductMatch[] {
  const terms = inferPartTerms(message);
  const make = normalizeSearchText(vehicle.make ?? "");
  const model = normalizeSearchText(vehicle.model ?? "");
  const year = vehicle.year;
  const hasVehicleIdentity = Boolean(make || model);

  return products
    .filter((product) => product.stock > 0)
    .map((product) => {
      let score = 0;
      const reasons: string[] = [];
      const haystack = normalizeSearchText(
        `${product.name} ${product.sku} ${product.brand ?? ""} ${product.description}`,
      );

      for (const term of terms) {
        if (haystack.includes(term)) {
          score += 0.18;
          reasons.push(`coincide con "${term}"`);
        }
      }

      const compatibilities = product.compatibility ?? [];
      const compatible = compatibilities.find((item) => {
        const makeOk = !make || normalizeSearchText(item.make).includes(make) || make.includes(normalizeSearchText(item.make));
        const modelOk =
          !model || normalizeSearchText(item.model).includes(model) || model.includes(normalizeSearchText(item.model));
        const yearOk = !year || ((!item.yearFrom || item.yearFrom <= year) && (!item.yearTo || item.yearTo >= year));
        return makeOk && modelOk && yearOk;
      });

      if (compatible) {
        score += 0.42;
        reasons.push("compatibilidad vehicular registrada");
        if (vehicle.engine && compatible.engine && normalizeSearchText(compatible.engine).includes(normalizeSearchText(vehicle.engine))) {
          score += 0.1;
          reasons.push("motor compatible");
        }
      } else if (compatibilities.length > 0 && (make || model || year)) {
        score -= 0.5;
      }

      if (product.stock > 0) {
        score += 0.08;
        reasons.push("hay stock disponible");
      }

      return {
        product,
        confidence: Math.max(0, Math.min(0.95, score)),
        reasons,
      };
    })
    .filter((match) => {
      if (hasVehicleIdentity) {
        return match.reasons.includes("compatibilidad vehicular registrada") && match.confidence >= 0.35;
      }
      return match.confidence > 0.08;
    })
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, 4);
}
