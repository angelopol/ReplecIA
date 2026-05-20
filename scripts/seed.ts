import { and, eq } from "drizzle-orm";
import { existsSync } from "node:fs";
import { loadEnvFile } from "node:process";
import { hashPassword } from "../lib/password";

if (existsSync(".env")) {
  loadEnvFile(".env");
}

async function main() {
  const { db } = await import("../lib/db");
  const { adminUsers, businesses, categories, products, vehicleCompatibilities } = await import("../lib/db/schema");
  const email = (process.env.ADMIN_SEED_EMAIL || "admin@replecia.local").toLowerCase();
  const password = process.env.ADMIN_SEED_PASSWORD || "admin12345";

  let business = await db.query.businesses.findFirst({ where: eq(businesses.slug, "demo") });
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

  const admin = await db.query.adminUsers.findFirst({ where: eq(adminUsers.email, email) });
  if (!admin) {
    await db.insert(adminUsers).values({
      businessId: business.id,
      email,
      passwordHash: hashPassword(password),
      name: "Administrador ReplecIA",
    });
  }

  const categoryIds = new Map<string, string>();
  for (const name of ["Frenos", "Electrico", "Enfriamiento", "Filtros", "Suspension", "Motor", "Sensores"]) {
    let category = await db.query.categories.findFirst({
      where: and(eq(categories.businessId, business.id), eq(categories.name, name)),
    });
    if (!category) {
      [category] = await db.insert(categories).values({ businessId: business.id, name }).returning();
    }
    categoryIds.set(name, category.id);
  }

  const catalog = [
    p("Frenos", "BRK-COR-09-13", "Pastillas de freno delanteras Toyota Corolla 2009-2013", "Akebono", "Juego de pastillas delanteras ceramicas para Corolla.", "38.00", 8, [
      c("Toyota", "Corolla", 2009, 2013, "1.8"),
    ]),
    p("Frenos", "BRK-AVE-07-18", "Pastillas de freno delanteras Chevrolet Aveo 2007-2018", "ACDelco", "Pastillas semimetalicas para Aveo.", "32.00", 10, [
      c("Chevrolet", "Aveo", 2007, 2018, "1.6"),
    ]),
    p("Electrico", "ALT-COR-09-13", "Alternador Toyota Corolla 2009-2013 1.8", "Denso", "Alternador 12V compatible con Corolla 1.8.", "145.00", 4, [
      c("Toyota", "Corolla", 2009, 2013, "1.8"),
    ]),
    p("Electrico", "ALT-ACC-98-05", "Alternador Hyundai Accent 1998-2005", "Valeo", "Alternador 12V para Accent.", "118.00", 5, [
      c("Hyundai", "Accent", 1998, 2005, "1.5"),
    ]),
    p("Electrico", "ALT-AVE-07-18", "Alternador Chevrolet Aveo 2007-2018", "ACDelco", "Alternador para Aveo 1.6 con polea.", "112.00", 6, [
      c("Chevrolet", "Aveo", 2007, 2018, "1.6"),
    ]),
    p("Enfriamiento", "RAD-AVE-07-18", "Radiador Chevrolet Aveo 2007-2018", "Koyorad", "Radiador de aluminio para sistema de enfriamiento.", "92.00", 7, [
      c("Chevrolet", "Aveo", 2007, 2018, "1.6"),
    ]),
    p("Enfriamiento", "RAD-CIV-06-11", "Radiador Honda Civic 2006-2011", "Denso", "Radiador compatible con Civic octava generacion.", "128.00", 3, [
      c("Honda", "Civic", 2006, 2011, "1.8"),
    ]),
    p("Filtros", "FLT-OIL-COR-09-19", "Filtro de aceite Toyota Corolla 2009-2019", "WIX", "Filtro de aceite para mantenimiento preventivo.", "9.50", 24, [
      c("Toyota", "Corolla", 2009, 2019, "1.8"),
      c("Toyota", "Yaris", 2006, 2018, "1.5"),
    ]),
    p("Suspension", "SHK-SEN-07-12", "Amortiguador delantero Nissan Sentra 2007-2012", "KYB", "Amortiguador delantero para Sentra B16.", "74.00", 6, [
      c("Nissan", "Sentra", 2007, 2012, "2.0"),
    ]),
    p("Suspension", "SHK-RIO-12-17", "Amortiguador delantero Kia Rio 2012-2017", "Monroe", "Amortiguador delantero para Rio.", "69.00", 5, [
      c("Kia", "Rio", 2012, 2017, "1.4"),
    ]),
    p("Motor", "BELT-COR-09-13", "Correa unica Toyota Corolla 2009-2013", "Gates", "Correa de accesorios para motor 1.8.", "24.00", 14, [
      c("Toyota", "Corolla", 2009, 2013, "1.8"),
    ]),
    p("Motor", "SPK-CIV-06-11", "Bujias Honda Civic 2006-2011 juego x4", "NGK", "Juego de bujias para Civic 1.8.", "34.00", 12, [
      c("Honda", "Civic", 2006, 2011, "1.8"),
    ]),
    p("Sensores", "SEN-O2-EXPL-02-05", "Sensor de oxigeno Ford Explorer 2002-2005", "Bosch", "Sensor O2 para mezcla y consumo.", "58.00", 4, [
      c("Ford", "Explorer", 2002, 2005, "4.0"),
    ]),
    p("Sensores", "SEN-CKP-CHER-99-04", "Sensor cigueñal Jeep Cherokee 1999-2004", "Mopar", "Sensor CKP para fallas de encendido.", "46.00", 4, [
      c("Jeep", "Cherokee", 1999, 2004, "4.0"),
    ]),
  ];

  for (const item of catalog) {
    let product = await db.query.products.findFirst({
      where: and(eq(products.businessId, business.id), eq(products.sku, item.sku)),
    });
    if (!product) {
      [product] = await db
        .insert(products)
        .values({
          businessId: business.id,
          categoryId: categoryIds.get(item.category),
          sku: item.sku,
          name: item.name,
          brand: item.brand,
          description: item.description,
          priceUsd: item.priceUsd,
          stock: item.stock,
          technicalSpecs: {},
        })
        .returning();
    }

    for (const compat of item.compat) {
      const existing = await db.query.vehicleCompatibilities.findFirst({
        where: and(
          eq(vehicleCompatibilities.productId, product.id),
          eq(vehicleCompatibilities.make, compat.make),
          eq(vehicleCompatibilities.model, compat.model),
        ),
      });
      if (!existing) {
        await db.insert(vehicleCompatibilities).values({ productId: product.id, ...compat });
      }
    }
  }

  console.log(`Seed listo. Admin: ${email} / ${password}. Productos cargados: ${catalog.length}`);
}

function p(
  category: string,
  sku: string,
  name: string,
  brand: string,
  description: string,
  priceUsd: string,
  stock: number,
  compat: ReturnType<typeof c>[],
) {
  return { category, sku, name, brand, description, priceUsd, stock, compat };
}

function c(make: string, model: string, yearFrom: number, yearTo: number, engine: string) {
  return { make, model, yearFrom, yearTo, engine };
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
