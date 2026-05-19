CREATE TYPE "public"."conversation_status" AS ENUM('open', 'needs_human', 'converted', 'closed');--> statement-breakpoint
CREATE TYPE "public"."order_status" AS ENUM('draft', 'quote_requested', 'pending', 'confirmed', 'ready', 'delivered', 'cancelled');--> statement-breakpoint
CREATE TABLE "admin_users" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"business_id" uuid NOT NULL,
	"email" varchar(180) NOT NULL,
	"password_hash" text NOT NULL,
	"name" varchar(120) DEFAULT 'Administrador' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "admin_users_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE TABLE "businesses" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(160) NOT NULL,
	"slug" varchar(80) NOT NULL,
	"phone" varchar(40),
	"address" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "businesses_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "categories" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"business_id" uuid NOT NULL,
	"name" varchar(120) NOT NULL
);
--> statement-breakpoint
CREATE TABLE "conversations" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"business_id" uuid NOT NULL,
	"customer_name" varchar(140),
	"customer_phone" varchar(50),
	"status" "conversation_status" DEFAULT 'open' NOT NULL,
	"vehicle" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"diagnosis" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "messages" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"conversation_id" uuid NOT NULL,
	"role" varchar(20) NOT NULL,
	"content" text NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "order_items" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"order_id" uuid NOT NULL,
	"product_id" uuid NOT NULL,
	"quantity" integer DEFAULT 1 NOT NULL,
	"unit_price_usd" numeric(10, 2) NOT NULL
);
--> statement-breakpoint
CREATE TABLE "orders" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"business_id" uuid NOT NULL,
	"conversation_id" uuid,
	"customer_name" varchar(140) NOT NULL,
	"customer_phone" varchar(50) NOT NULL,
	"status" "order_status" DEFAULT 'quote_requested' NOT NULL,
	"notes" text DEFAULT '' NOT NULL,
	"total_usd" numeric(10, 2) DEFAULT '0' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "products" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"business_id" uuid NOT NULL,
	"category_id" uuid,
	"sku" varchar(80) NOT NULL,
	"name" varchar(180) NOT NULL,
	"brand" varchar(120),
	"description" text DEFAULT '' NOT NULL,
	"technical_specs" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"price_usd" numeric(10, 2) DEFAULT '0' NOT NULL,
	"stock" integer DEFAULT 0 NOT NULL,
	"active" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "vehicle_compatibilities" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"product_id" uuid NOT NULL,
	"make" varchar(100) NOT NULL,
	"model" varchar(120) NOT NULL,
	"year_from" integer,
	"year_to" integer,
	"engine" varchar(120),
	"notes" text
);
--> statement-breakpoint
ALTER TABLE "admin_users" ADD CONSTRAINT "admin_users_business_id_businesses_id_fk" FOREIGN KEY ("business_id") REFERENCES "public"."businesses"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "categories" ADD CONSTRAINT "categories_business_id_businesses_id_fk" FOREIGN KEY ("business_id") REFERENCES "public"."businesses"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "conversations" ADD CONSTRAINT "conversations_business_id_businesses_id_fk" FOREIGN KEY ("business_id") REFERENCES "public"."businesses"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "messages" ADD CONSTRAINT "messages_conversation_id_conversations_id_fk" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "order_items" ADD CONSTRAINT "order_items_order_id_orders_id_fk" FOREIGN KEY ("order_id") REFERENCES "public"."orders"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "order_items" ADD CONSTRAINT "order_items_product_id_products_id_fk" FOREIGN KEY ("product_id") REFERENCES "public"."products"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "orders" ADD CONSTRAINT "orders_business_id_businesses_id_fk" FOREIGN KEY ("business_id") REFERENCES "public"."businesses"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "orders" ADD CONSTRAINT "orders_conversation_id_conversations_id_fk" FOREIGN KEY ("conversation_id") REFERENCES "public"."conversations"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "products" ADD CONSTRAINT "products_business_id_businesses_id_fk" FOREIGN KEY ("business_id") REFERENCES "public"."businesses"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "products" ADD CONSTRAINT "products_category_id_categories_id_fk" FOREIGN KEY ("category_id") REFERENCES "public"."categories"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "vehicle_compatibilities" ADD CONSTRAINT "vehicle_compatibilities_product_id_products_id_fk" FOREIGN KEY ("product_id") REFERENCES "public"."products"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "admin_users_business_idx" ON "admin_users" USING btree ("business_id");--> statement-breakpoint
CREATE INDEX "categories_business_idx" ON "categories" USING btree ("business_id");--> statement-breakpoint
CREATE INDEX "conversations_business_idx" ON "conversations" USING btree ("business_id");--> statement-breakpoint
CREATE INDEX "messages_conversation_idx" ON "messages" USING btree ("conversation_id");--> statement-breakpoint
CREATE INDEX "order_items_order_idx" ON "order_items" USING btree ("order_id");--> statement-breakpoint
CREATE INDEX "orders_business_idx" ON "orders" USING btree ("business_id");--> statement-breakpoint
CREATE INDEX "orders_conversation_idx" ON "orders" USING btree ("conversation_id");--> statement-breakpoint
CREATE INDEX "products_business_idx" ON "products" USING btree ("business_id");--> statement-breakpoint
CREATE INDEX "products_sku_idx" ON "products" USING btree ("sku");--> statement-breakpoint
CREATE INDEX "vehicle_compatibilities_product_idx" ON "vehicle_compatibilities" USING btree ("product_id");--> statement-breakpoint
CREATE INDEX "vehicle_compatibilities_vehicle_idx" ON "vehicle_compatibilities" USING btree ("make","model");