import { revalidatePath } from "next/cache";
import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { moneyUsd, shortDate } from "@/lib/format";
import { listOrders, updateDeliveryStatus, updateOrderStatus } from "@/lib/services/orders";

async function updateStatusAction(formData: FormData) {
  "use server";
  await updateOrderStatus({
    orderId: formData.get("orderId"),
    status: formData.get("status"),
  });
  revalidatePath("/admin/orders");
  revalidatePath("/admin/inventory");
}

async function updateDeliveryAction(formData: FormData) {
  "use server";
  await updateDeliveryStatus({
    orderId: formData.get("orderId"),
    deliveryStatus: formData.get("deliveryStatus"),
  });
  revalidatePath("/admin/orders");
}

export default async function OrdersPage() {
  const session = await getCurrentSession();
  const rows = await listOrders(session!.businessId);

  return (
    <AdminShell>
      <div className="topbar">
        <div>
          <h1 className="title">Pedidos</h1>
          <p className="subtitle">Solicitudes creadas por el chatbot y estado operativo.</p>
        </div>
      </div>

      <section className="panel">
        <table className="table">
          <thead>
            <tr>
              <th>Pedido</th>
              <th>Cliente</th>
              <th>Items</th>
              <th>Total</th>
              <th>Estado</th>
              <th>Entrega / Pago</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((order) => (
              <tr key={order.id}>
                <td>
                  <strong>{order.id.slice(0, 8)}</strong>
                  <br />
                  <span className="subtitle">{shortDate(order.createdAt)}</span>
                </td>
                <td>
                  {order.customerName}
                  <br />
                  <span className="subtitle">{order.customerPhone}</span>
                </td>
                <td>
                  {order.items.map((item) => (
                    <div key={item.id}>
                      {item.quantity} x {item.product.name}
                    </div>
                  ))}
                </td>
                <td>{moneyUsd(order.totalUsd)}</td>
                <td>
                  <span className="badge">{order.status}</span>
                </td>
                <td>
                  <div>{order.deliveryAddress || "Pendiente"}</div>
                  <span className={order.paymentStatus === "paid" ? "badge ok" : "badge warn"}>
                    {order.paymentStatus}
                  </span>
                  <span className="badge" style={{ marginLeft: 6 }}>
                    {order.deliveryStatus}
                  </span>
                  {order.paymentReference ? <div className="subtitle">{order.paymentReference}</div> : null}
                </td>
                <td>
                  <form action={updateStatusAction} style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    <input type="hidden" name="orderId" value={order.id} />
                    <select name="status" defaultValue={order.status}>
                      <option value="quote_requested">Cotización</option>
                      <option value="pending">Pendiente</option>
                      <option value="confirmed">Confirmado</option>
                      <option value="ready">Listo</option>
                      <option value="delivered">Entregado</option>
                      <option value="cancelled">Cancelado</option>
                    </select>
                    <button className="btn secondary" type="submit">
                      Guardar
                    </button>
                  </form>
                  <form action={updateDeliveryAction} style={{ display: "flex", gap: 8 }}>
                    <input type="hidden" name="orderId" value={order.id} />
                    <select name="deliveryStatus" defaultValue={order.deliveryStatus}>
                      <option value="pending_coordination">Por coordinar</option>
                      <option value="coordinated">Coordinado</option>
                      <option value="in_transit">En camino</option>
                      <option value="delivered">Entregado</option>
                    </select>
                    <button className="btn secondary" type="submit">
                      Entrega
                    </button>
                  </form>
                </td>
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7}>Aún no hay pedidos.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
