import { NextRequest, NextResponse } from "next/server";
import { verifyAdminSessionEdge } from "@/lib/auth-edge";

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (!pathname.startsWith("/admin") || pathname === "/admin/login") {
    return NextResponse.next();
  }

  const token = request.cookies.get("replecia_session")?.value;
  const session = token ? await verifyAdminSessionEdge(token) : null;

  if (!session) {
    const loginUrl = new URL("/admin/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*"],
};
