import { NextResponse } from "next/server";
import { getDefaultBusiness } from "@/lib/db/defaults";
import { createOrder } from "@/lib/services/orders";

export async function POST(request: Request) {
  const business = await getDefaultBusiness();
  const body = await request.json();
  const order = await createOrder(business.id, body);
  return NextResponse.json({ order });
}
