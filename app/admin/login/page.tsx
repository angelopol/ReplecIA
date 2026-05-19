import { eq } from "drizzle-orm";
import { redirect } from "next/navigation";
import { db } from "@/lib/db";
import { adminUsers } from "@/lib/db/schema";
import { setSessionCookie, verifyPassword } from "@/lib/auth";

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
    <main className="login">
      <form className="card grid" action={loginAction}>
        <div>
          <h1 className="title">Entrar a ReplecIA</h1>
          <p className="subtitle">Panel de gestión para la tienda de autopartes.</p>
        </div>
        {params.error ? <span className="badge warn">Credenciales incorrectas</span> : null}
        <input type="hidden" name="next" value={params.next || "/admin"} />
        <div className="field">
          <label htmlFor="email">Correo</label>
          <input id="email" name="email" type="email" required autoComplete="email" />
        </div>
        <div className="field">
          <label htmlFor="password">Contraseña</label>
          <input id="password" name="password" type="password" required autoComplete="current-password" />
        </div>
        <button className="btn" type="submit">
          Entrar
        </button>
      </form>
    </main>
  );
}
