import { z } from "zod";

export const createOrderSchema = z.object({
  conversationId: z.string().uuid().optional(),
  customerName: z.string().trim().min(2).max(140),
  customerPhone: z.string().trim().min(5).max(50),
  productId: z.string().uuid(),
  quantity: z.coerce.number().int().min(1).max(99),
  deliveryAddress: z.string().trim().min(2).max(500).optional(),
  paymentReference: z.string().trim().min(2).max(120).optional(),
  notes: z.string().trim().max(1500).optional(),
});

export const updateOrderStatusSchema = z.object({
  orderId: z.string().uuid(),
  status: z.enum(["quote_requested", "pending", "confirmed", "ready", "delivered", "cancelled"]),
});

export const updateDeliveryStatusSchema = z.object({
  orderId: z.string().uuid(),
  deliveryStatus: z.enum(["pending_coordination", "coordinated", "in_transit", "delivered"]),
});
