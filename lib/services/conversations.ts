import { desc, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { conversations, messages, type DiagnosisSnapshot, type VehicleInfo } from "@/lib/db/schema";

export async function listConversations(businessId: string) {
  return db.query.conversations.findMany({
    where: eq(conversations.businessId, businessId),
    with: {
      messages: {
        orderBy: desc(messages.createdAt),
        limit: 1,
      },
    },
    orderBy: desc(conversations.updatedAt),
  });
}

export async function appendConversationMessage(input: {
  businessId: string;
  conversationId?: string;
  role: "user" | "assistant" | "system";
  content: string;
  vehicle?: VehicleInfo;
  diagnosis?: DiagnosisSnapshot;
}) {
  let conversationId = input.conversationId;

  if (!conversationId) {
    const [created] = await db.insert(conversations).values({ businessId: input.businessId }).returning();
    conversationId = created.id;
  }

  await db.insert(messages).values({
    conversationId,
    role: input.role,
    content: input.content,
  });

  if (input.vehicle || input.diagnosis) {
    await db
      .update(conversations)
      .set({
        vehicle: input.vehicle ?? {},
        diagnosis: input.diagnosis ?? {},
        status: input.diagnosis?.confidence && input.diagnosis.confidence < 0.28 ? "needs_human" : "open",
        updatedAt: sql`now()`,
      })
      .where(eq(conversations.id, conversationId));
  } else {
    await db.update(conversations).set({ updatedAt: sql`now()` }).where(eq(conversations.id, conversationId));
  }

  return conversationId;
}
