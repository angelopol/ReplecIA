import { count, desc, eq, sql } from "drizzle-orm";
import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { db } from "@/lib/db";
import { conversations, orders, products } from "@/lib/db/schema";
import { moneyUsd, shortDate } from "@/lib/format";

export default async function AdminDashboard() {
  const session = await getCurrentSession();
  const businessId = session!.businessId;

  const [productCount] = await db.select({ value: count() }).from(products).where(eq(products.businessId, businessId));
  const [orderCount] = await db.select({ value: count() }).from(orders).where(eq(orders.businessId, businessId));
  const [conversationCount] = await db
    .select({ value: count() })
    .from(conversations)
    .where(eq(conversations.businessId, businessId));
  const [sales] = await db
    .select({ value: sql<string>`coalesce(sum(${orders.totalUsd}), 0)` })
    .from(orders)
    .where(eq(orders.businessId, businessId));

  const latestOrders = await db.query.orders.findMany({
    where: eq(orders.businessId, businessId),
    orderBy: desc(orders.createdAt),
    limit: 6,
  });

  return (
    <AdminShell>
      <div className="topbar">
        <div>
          <h1 className="title">Dashboard</h1>
          <p className="subtitle">Resumen operativo de ventas, inventario y atención automatizada.</p>
        </div>
      </div>

      <section className="grid grid-3">
        <div className="card metric">
          Repuestos registrados
          <strong>{productCount.value}</strong>
        </div>
        <div className="card metric">
          Pedidos
          <strong>{orderCount.value}</strong>
        </div>
        <div className="card metric">
          Conversaciones
          <strong>{conversationCount.value}</strong>
        </div>
      </section>

      <section className="card metric" style={{ marginTop: 16 }}>
        Total cotizado
        <strong>{moneyUsd(sales.value)}</strong>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <table className="table">
          <thead>
            <tr>
              <th>Pedido</th>
              <th>Cliente</th>
              <th>Estado</th>
              <th>Total</th>
              <th>Fecha</th>
            </tr>
          </thead>
          <tbody>
            {latestOrders.map((order) => (
              <tr key={order.id}>
                <td>{order.id.slice(0, 8)}</td>
                <td>{order.customerName}</td>
                <td>
                  <span className="badge">{order.status}</span>
                </td>
                <td>{moneyUsd(order.totalUsd)}</td>
                <td>{shortDate(order.createdAt)}</td>
              </tr>
            ))}
            {latestOrders.length === 0 ? (
              <tr>
                <td colSpan={5}>Aún no hay pedidos.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
