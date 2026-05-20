import { NextResponse } from "next/server";
import { getDefaultBusiness } from "@/lib/db/defaults";
import { createOrder } from "@/lib/services/orders";

export async function POST(request: Request) {
  try {
    const business = await getDefaultBusiness();
    const body = await request.json();
    const order = await createOrder(business.id, body);
    return NextResponse.json({ order });
  } catch (error) {
    console.error("orders_api_error", error);
    return NextResponse.json(
      {
        error: "order_failed",
        message: error instanceof Error ? error.message : "No se pudo crear el pedido",
      },
      { status: 400 },
    );
  }
}
