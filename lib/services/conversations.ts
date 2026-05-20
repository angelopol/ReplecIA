import { and, asc, desc, eq, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { conversations, messages, type DiagnosisSnapshot, type SalesMemory, type VehicleInfo } from "@/lib/db/schema";

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

export async function getConversationDetail(businessId: string, conversationId: string) {
  return db.query.conversations.findFirst({
    where: and(eq(conversations.id, conversationId), eq(conversations.businessId, businessId)),
    with: {
      messages: {
        orderBy: asc(messages.createdAt),
      },
    },
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

export async function getConversationMemory(conversationId: string) {
  const conversation = await db.query.conversations.findFirst({
    where: eq(conversations.id, conversationId),
  });
  return conversation?.salesMemory ?? {};
}

export async function updateConversationMemory(conversationId: string, salesMemory: SalesMemory) {
  await db
    .update(conversations)
    .set({
      salesMemory,
      currentOrderId: salesMemory.lastOrderId ?? null,
      updatedAt: sql`now()`,
    })
    .where(eq(conversations.id, conversationId));
}

export async function adminReplyConversation(input: {
  businessId: string;
  conversationId: string;
  content: string;
}) {
  const content = input.content.trim();
  if (!content) {
    throw new Error("La respuesta no puede estar vacía.");
  }
  const conversation = await db.query.conversations.findFirst({
    where: and(eq(conversations.id, input.conversationId), eq(conversations.businessId, input.businessId)),
  });
  if (!conversation) {
    throw new Error("Conversación no encontrada.");
  }
  await db.insert(messages).values({
    conversationId: input.conversationId,
    role: "admin",
    content,
    metadata: { manual: true },
  });
  await db
    .update(conversations)
    .set({
      status: "open",
      updatedAt: sql`now()`,
    })
    .where(eq(conversations.id, input.conversationId));
}

export async function closeConversation(businessId: string, conversationId: string) {
  const conversation = await db.query.conversations.findFirst({
    where: and(eq(conversations.id, conversationId), eq(conversations.businessId, businessId)),
  });
  if (!conversation) {
    throw new Error("Conversación no encontrada.");
  }
  await db.insert(messages).values({
    conversationId,
    role: "system",
    content: "Conversación cerrada por administrador.",
    metadata: { manual: true, status: "closed" },
  });
  await db
    .update(conversations)
    .set({
      status: "closed",
      updatedAt: sql`now()`,
    })
    .where(eq(conversations.id, conversationId));
}
