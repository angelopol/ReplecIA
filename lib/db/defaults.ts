import { eq } from "drizzle-orm";
import { db } from "@/lib/db";
import { businesses } from "@/lib/db/schema";

export async function getDefaultBusiness() {
  const existing = await db.query.businesses.findFirst({
    where: eq(businesses.slug, "demo"),
  });

  if (existing) {
    return existing;
  }

  const [created] = await db
    .insert(businesses)
    .values({
      name: "ReplecIA Demo",
      slug: "demo",
      phone: "+58 000-0000000",
      address: "Tienda de autopartes",
    })
    .returning();

  return created;
}
