import {
  boolean,
  index,
  integer,
  jsonb,
  numeric,
  pgEnum,
  pgTable,
  text,
  timestamp,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";

export const orderStatusEnum = pgEnum("order_status", [
  "draft",
  "quote_requested",
  "pending",
  "confirmed",
  "ready",
  "delivered",
  "cancelled",
]);

export const conversationStatusEnum = pgEnum("conversation_status", [
  "open",
  "needs_human",
  "converted",
  "closed",
]);

export const paymentStatusEnum = pgEnum("payment_status", ["pending", "paid", "failed", "refunded"]);
export const checkoutStatusEnum = pgEnum("checkout_status", [
  "collecting_contact",
  "collecting_delivery",
  "requires_payment_method",
  "processing",
  "succeeded",
  "failed",
  "cancelled",
]);

export const businesses = pgTable("businesses", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: varchar("name", { length: 160 }).notNull(),
  slug: varchar("slug", { length: 80 }).notNull().unique(),
  phone: varchar("phone", { length: 40 }),
  address: text("address"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const adminUsers = pgTable(
  "admin_users",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    email: varchar("email", { length: 180 }).notNull().unique(),
    passwordHash: text("password_hash").notNull(),
    name: varchar("name", { length: 120 }).notNull().default("Administrador"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    businessIdx: index("admin_users_business_idx").on(table.businessId),
  }),
);

export const categories = pgTable(
  "categories",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    name: varchar("name", { length: 120 }).notNull(),
  },
  (table) => ({
    businessIdx: index("categories_business_idx").on(table.businessId),
  }),
);

export const products = pgTable(
  "products",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    categoryId: uuid("category_id").references(() => categories.id, { onDelete: "set null" }),
    sku: varchar("sku", { length: 80 }).notNull(),
    name: varchar("name", { length: 180 }).notNull(),
    brand: varchar("brand", { length: 120 }),
    description: text("description").notNull().default(""),
    technicalSpecs: jsonb("technical_specs").$type<Record<string, string>>().notNull().default({}),
    priceUsd: numeric("price_usd", { precision: 10, scale: 2 }).notNull().default("0"),
    stock: integer("stock").notNull().default(0),
    active: boolean("active").notNull().default(true),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    businessIdx: index("products_business_idx").on(table.businessId),
    skuIdx: index("products_sku_idx").on(table.sku),
  }),
);

export const vehicleCompatibilities = pgTable(
  "vehicle_compatibilities",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    productId: uuid("product_id").notNull().references(() => products.id, { onDelete: "cascade" }),
    make: varchar("make", { length: 100 }).notNull(),
    model: varchar("model", { length: 120 }).notNull(),
    yearFrom: integer("year_from"),
    yearTo: integer("year_to"),
    engine: varchar("engine", { length: 120 }),
    notes: text("notes"),
  },
  (table) => ({
    productIdx: index("vehicle_compatibilities_product_idx").on(table.productId),
    vehicleIdx: index("vehicle_compatibilities_vehicle_idx").on(table.make, table.model),
  }),
);

export const conversations = pgTable(
  "conversations",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    customerName: varchar("customer_name", { length: 140 }),
    customerPhone: varchar("customer_phone", { length: 50 }),
    status: conversationStatusEnum("status").notNull().default("open"),
    vehicle: jsonb("vehicle").$type<VehicleInfo>().notNull().default({}),
    diagnosis: jsonb("diagnosis").$type<DiagnosisSnapshot>().notNull().default({}),
    salesMemory: jsonb("sales_memory").$type<SalesMemory>().notNull().default({}),
    currentOrderId: uuid("current_order_id"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    businessIdx: index("conversations_business_idx").on(table.businessId),
  }),
);

export const checkoutSessions = pgTable(
  "checkout_sessions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    conversationId: uuid("conversation_id").notNull().references(() => conversations.id, { onDelete: "cascade" }),
    productId: uuid("product_id").notNull().references(() => products.id, { onDelete: "restrict" }),
    quantity: integer("quantity").notNull().default(1),
    status: checkoutStatusEnum("status").notNull().default("collecting_contact"),
    customerName: varchar("customer_name", { length: 140 }),
    customerPhone: varchar("customer_phone", { length: 50 }),
    deliveryAddress: text("delivery_address"),
    amountUsd: numeric("amount_usd", { precision: 10, scale: 2 }).notNull().default("0"),
    metadata: jsonb("metadata").$type<Record<string, unknown>>().notNull().default({}),
    expiresAt: timestamp("expires_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    businessIdx: index("checkout_sessions_business_idx").on(table.businessId),
    conversationIdx: index("checkout_sessions_conversation_idx").on(table.conversationId),
  }),
);

export const payments = pgTable(
  "payments",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    checkoutSessionId: uuid("checkout_session_id").notNull().references(() => checkoutSessions.id, { onDelete: "cascade" }),
    orderId: uuid("order_id").references(() => orders.id, { onDelete: "set null" }),
    status: paymentStatusEnum("status").notNull().default("pending"),
    amountUsd: numeric("amount_usd", { precision: 10, scale: 2 }).notNull().default("0"),
    reference: varchar("reference", { length: 120 }).notNull(),
    cardLast4: varchar("card_last4", { length: 4 }),
    paidAt: timestamp("paid_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    checkoutIdx: index("payments_checkout_idx").on(table.checkoutSessionId),
    orderIdx: index("payments_order_idx").on(table.orderId),
  }),
);

export const messages = pgTable(
  "messages",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    conversationId: uuid("conversation_id").notNull().references(() => conversations.id, { onDelete: "cascade" }),
    role: varchar("role", { length: 20 }).notNull(),
    content: text("content").notNull(),
    metadata: jsonb("metadata").$type<Record<string, unknown>>().notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    conversationIdx: index("messages_conversation_idx").on(table.conversationId),
  }),
);

export const orders = pgTable(
  "orders",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    businessId: uuid("business_id").notNull().references(() => businesses.id, { onDelete: "cascade" }),
    conversationId: uuid("conversation_id").references(() => conversations.id, { onDelete: "set null" }),
    customerName: varchar("customer_name", { length: 140 }).notNull(),
    customerPhone: varchar("customer_phone", { length: 50 }).notNull(),
    status: orderStatusEnum("status").notNull().default("quote_requested"),
    paymentStatus: paymentStatusEnum("payment_status").notNull().default("pending"),
    paymentReference: varchar("payment_reference", { length: 120 }),
    deliveryAddress: text("delivery_address"),
    deliveryStatus: varchar("delivery_status", { length: 80 }).notNull().default("pending_coordination"),
    notes: text("notes").notNull().default(""),
    totalUsd: numeric("total_usd", { precision: 10, scale: 2 }).notNull().default("0"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    businessIdx: index("orders_business_idx").on(table.businessId),
    conversationIdx: index("orders_conversation_idx").on(table.conversationId),
  }),
);

export const orderItems = pgTable(
  "order_items",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    orderId: uuid("order_id").notNull().references(() => orders.id, { onDelete: "cascade" }),
    productId: uuid("product_id").notNull().references(() => products.id, { onDelete: "restrict" }),
    quantity: integer("quantity").notNull().default(1),
    unitPriceUsd: numeric("unit_price_usd", { precision: 10, scale: 2 }).notNull(),
  },
  (table) => ({
    orderIdx: index("order_items_order_idx").on(table.orderId),
  }),
);

export const productRelations = relations(products, ({ many, one }) => ({
  compatibilities: many(vehicleCompatibilities),
  category: one(categories, {
    fields: [products.categoryId],
    references: [categories.id],
  }),
}));

export const vehicleCompatibilityRelations = relations(vehicleCompatibilities, ({ one }) => ({
  product: one(products, {
    fields: [vehicleCompatibilities.productId],
    references: [products.id],
  }),
}));

export const orderRelations = relations(orders, ({ many }) => ({
  items: many(orderItems),
}));

export const orderItemRelations = relations(orderItems, ({ one }) => ({
  order: one(orders, {
    fields: [orderItems.orderId],
    references: [orders.id],
  }),
  product: one(products, {
    fields: [orderItems.productId],
    references: [products.id],
  }),
}));

export const conversationRelations = relations(conversations, ({ many }) => ({
  messages: many(messages),
  checkoutSessions: many(checkoutSessions),
}));

export const messageRelations = relations(messages, ({ one }) => ({
  conversation: one(conversations, {
    fields: [messages.conversationId],
    references: [conversations.id],
  }),
}));

export const categoryRelations = relations(categories, ({ many }) => ({
  products: many(products),
}));

export const checkoutSessionRelations = relations(checkoutSessions, ({ one }) => ({
  conversation: one(conversations, {
    fields: [checkoutSessions.conversationId],
    references: [conversations.id],
  }),
  product: one(products, {
    fields: [checkoutSessions.productId],
    references: [products.id],
  }),
}));

export type VehicleInfo = {
  make?: string;
  model?: string;
  year?: number;
  engine?: string;
};

export type DiagnosisSnapshot = {
  symptom?: string;
  requestedPart?: string;
  confidence?: number;
  recommendation?: string;
  missingFields?: string[];
};

export type SalesMemory = {
  stage?: "idle" | "contact" | "delivery" | "payment" | "completed";
  selectedProductId?: string;
  selectedProductName?: string;
  selectedSku?: string;
  selectedPriceUsd?: string;
  selectedStock?: number;
  quantity?: number;
  customerName?: string;
  customerPhone?: string;
  deliveryAddress?: string;
  checkoutSessionId?: string;
  lastOrderId?: string;
  lastPaymentReference?: string;
  recentSummary?: string;
};
