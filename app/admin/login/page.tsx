import { eq } from "drizzle-orm";
import { redirect } from "next/navigation";
import Link from "next/link";
import { db } from "@/lib/db";
import { adminUsers } from "@/lib/db/schema";
import { setSessionCookie, verifyPassword } from "@/lib/auth";
import RepleciaLogo from "@/components/brand/RepleciaLogo";

async function loginAction(formData: FormData) {
  "use server";

  const email = String(formData.get("email") || "").trim().toLowerCase();
  const password = String(formData.get("password") || "");
  const next = String(formData.get("next") || "/admin");

  const user = await db.query.adminUsers.findFirst({
    where: eq(adminUsers.email, email),
  });

  if (!user || !verifyPassword(password, user.passwordHash)) {
    redirect("/admin/login?error=1");
  }

  await setSessionCookie({
    adminId: user.id,
    businessId: user.businessId,
    email: user.email,
  });

  redirect(next.startsWith("/admin") ? next : "/admin");
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const params = await searchParams;
  return (
    <main className="admin-login-page">
      <section className="admin-login-brand">
        <Link href="/" className="admin-login-brand__logo">
          <RepleciaLogo />
        </Link>
        <div>
          <p className="eyebrow">Panel comercial</p>
          <h1>Controla ventas, inventario y conversaciones desde un solo lugar</h1>
          <p>
            ReplecIA centraliza pedidos pagados, coordinación de entrega, asesoría IA y atención manual para que la
            tienda no pierda oportunidades.
          </p>
        </div>
        <div className="login-proof-grid">
          <span>Inventario en tiempo real</span>
          <span>Conversaciones trazables</span>
          <span>Pagos y entrega</span>
        </div>
      </section>

      <section className="admin-login-panel">
        <form className="login-card" action={loginAction}>
          <div>
            <RepleciaLogo className="login-logo" />
            <h2>Entrar al panel</h2>
            <p>Acceso privado para administradores de la tienda.</p>
          </div>
          {params.error ? <span className="badge warn">Credenciales incorrectas</span> : null}
          <input type="hidden" name="next" value={params.next || "/admin"} />
          <div className="field">
            <label htmlFor="email">Correo</label>
            <input id="email" name="email" type="email" required autoComplete="email" placeholder="admin@tienda.com" />
          </div>
          <div className="field">
            <label htmlFor="password">Contraseña</label>
            <input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
              placeholder="••••••••"
            />
          </div>
          <button className="btn orange" type="submit">
            Entrar
          </button>
          <div className="login-security-note">Sesión protegida para gestión operativa del negocio.</div>
        </form>
      </section>
    </main>
  );
}
