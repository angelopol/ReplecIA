import { and, desc, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { categories, products, vehicleCompatibilities } from "@/lib/db/schema";
import { productFormSchema } from "@/lib/validators/product";

export async function listInventory(businessId: string) {
  return db.query.products.findMany({
    where: eq(products.businessId, businessId),
    with: {
      compatibilities: true,
      category: true,
    },
    orderBy: desc(products.createdAt),
  });
}

export async function listAvailableProducts(businessId: string) {
  const rows = await db.query.products.findMany({
    where: and(eq(products.businessId, businessId), eq(products.active, true)),
    with: {
      compatibilities: true,
    },
    orderBy: desc(products.createdAt),
  });

  return rows.map((product) => ({
    id: product.id,
    name: product.name,
    sku: product.sku,
    brand: product.brand,
    description: product.description,
    priceUsd: product.priceUsd,
    stock: product.stock,
    compatibility: product.compatibilities.map((item) => ({
      make: item.make,
      model: item.model,
      yearFrom: item.yearFrom,
      yearTo: item.yearTo,
      engine: item.engine,
    })),
  }));
}

export async function createInventoryItem(businessId: string, formData: FormData) {
  const parsed = productFormSchema.parse(Object.fromEntries(formData));
  const categoryName = parsed.category?.trim();
  let categoryId: string | null = null;

  if (categoryName) {
    const existing = await db.query.categories.findFirst({
      where: and(eq(categories.businessId, businessId), eq(categories.name, categoryName)),
    });
    if (existing) {
      categoryId = existing.id;
    } else {
      const [created] = await db
        .insert(categories)
        .values({ businessId, name: categoryName })
        .returning({ id: categories.id });
      categoryId = created.id;
    }
  }

  const [created] = await db
    .insert(products)
    .values({
      businessId,
      categoryId,
      sku: parsed.sku,
      name: parsed.name,
      brand: parsed.brand || null,
      description: parsed.description || "",
      priceUsd: parsed.priceUsd.toFixed(2),
      stock: parsed.stock,
      technicalSpecs: {},
    })
    .returning();

  if (parsed.make && parsed.model) {
    await db.insert(vehicleCompatibilities).values({
      productId: created.id,
      make: parsed.make,
      model: parsed.model,
      yearFrom: parsed.yearFrom,
      yearTo: parsed.yearTo,
      engine: parsed.engine || null,
    });
  }

  return created;
}

export async function updateStock(productId: string, nextStock: number) {
  await db
    .update(products)
    .set({ stock: Math.max(0, nextStock), updatedAt: sql`now()` })
    .where(eq(products.id, productId));
}
