import Link from "next/link";
import { redirect } from "next/navigation";
import { clearSessionCookie, getCurrentSession } from "@/lib/auth";
import RepleciaLogo from "@/components/brand/RepleciaLogo";

async function logoutAction() {
  "use server";
  await clearSessionCookie();
  redirect("/admin/login");
}

export default async function AdminShell({ children }: { children: React.ReactNode }) {
  const session = await getCurrentSession();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/admin" className="sidebar-brand">
          <RepleciaLogo />
          <span>Panel comercial</span>
        </Link>
        <nav className="nav">
          <Link href="/admin">Dashboard</Link>
          <Link href="/admin/inventory">Inventario</Link>
          <Link href="/admin/orders">Pedidos</Link>
          <Link href="/admin/conversations">Conversaciones</Link>
          <Link href="/">Chat web</Link>
          <form action={logoutAction}>
            <button type="submit">Salir {session?.email ? `(${session.email})` : ""}</button>
          </form>
        </nav>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
