import { z } from "zod";

export const productFormSchema = z.object({
  sku: z.string().trim().min(1, "El SKU es obligatorio").max(80),
  name: z.string().trim().min(2, "El nombre es obligatorio").max(180),
  brand: z.string().trim().max(120).optional(),
  category: z.string().trim().max(120).optional(),
  description: z.string().trim().max(2000).optional(),
  priceUsd: z.coerce.number().min(0).max(999999),
  stock: z.coerce.number().int().min(0).max(999999),
  make: z.string().trim().max(100).optional(),
  model: z.string().trim().max(120).optional(),
  yearFrom: z.coerce.number().int().min(1900).max(2100).optional().or(z.literal("").transform(() => undefined)),
  yearTo: z.coerce.number().int().min(1900).max(2100).optional().or(z.literal("").transform(() => undefined)),
  engine: z.string().trim().max(120).optional(),
});

export type ProductFormInput = z.infer<typeof productFormSchema>;
