import { eq } from "drizzle-orm";
import { db } from "../lib/db";
import { adminUsers, businesses, categories, products, vehicleCompatibilities } from "../lib/db/schema";
import { hashPassword } from "../lib/password";

async function main() {
  const email = (process.env.ADMIN_SEED_EMAIL || "admin@replecia.local").toLowerCase();
  const password = process.env.ADMIN_SEED_PASSWORD || "admin12345";

  let business = await db.query.businesses.findFirst({
    where: eq(businesses.slug, "demo"),
  });

  if (!business) {
    [business] = await db
      .insert(businesses)
      .values({
        name: "ReplecIA Demo",
        slug: "demo",
        phone: "+58 000-0000000",
        address: "Valencia, Venezuela",
      })
      .returning();
  }

  const admin = await db.query.adminUsers.findFirst({
    where: eq(adminUsers.email, email),
  });

  if (!admin) {
    await db.insert(adminUsers).values({
      businessId: business.id,
      email,
      passwordHash: hashPassword(password),
      name: "Administrador ReplecIA",
    });
  }

  const existingProduct = await db.query.products.findFirst({
    where: eq(products.businessId, business.id),
  });

  if (!existingProduct) {
    const [category] = await db
      .insert(categories)
      .values({ businessId: business.id, name: "Frenos" })
      .returning();

    const [product] = await db
      .insert(products)
      .values({
        businessId: business.id,
        categoryId: category.id,
        sku: "BRK-COR-09-13",
        name: "Pastillas de freno delanteras Toyota Corolla 2009-2013",
        brand: "Akebono",
        description: "Juego de pastillas delanteras cerámicas para Corolla.",
        priceUsd: "38.00",
        stock: 8,
      })
      .returning();

    await db.insert(vehicleCompatibilities).values({
      productId: product.id,
      make: "Toyota",
      model: "Corolla",
      yearFrom: 2009,
      yearTo: 2013,
      engine: "1.8",
    });
  }

  console.log(`Seed listo. Admin: ${email} / ${password}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
