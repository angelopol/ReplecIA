import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { shortDate } from "@/lib/format";
import { listConversations } from "@/lib/services/conversations";
import Link from "next/link";

export default async function ConversationsPage() {
  const session = await getCurrentSession();
  const rows = await listConversations(session!.businessId);

  return (
    <AdminShell>
      <div className="topbar">
        <div>
          <h1 className="title">Conversaciones</h1>
          <p className="subtitle">Historial del asesor IA y alertas para revisión humana.</p>
        </div>
      </div>

      <section className="panel">
        <table className="table">
          <thead>
            <tr>
              <th>Conversación</th>
              <th>Vehículo</th>
              <th>Diagnóstico</th>
              <th>Venta</th>
              <th>Último mensaje</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((conversation) => (
              <tr key={conversation.id}>
                <td>
                  <Link href={`/admin/conversations/${conversation.id}`}>
                    <strong>{conversation.id.slice(0, 8)}</strong>
                  </Link>
                  <br />
                  <span className="subtitle">{shortDate(conversation.updatedAt)}</span>
                </td>
                <td>
                  {[conversation.vehicle.make, conversation.vehicle.model, conversation.vehicle.year, conversation.vehicle.engine]
                    .filter(Boolean)
                    .join(" ") || "Sin datos completos"}
                </td>
                <td>{conversation.diagnosis.recommendation || conversation.diagnosis.symptom || "Pendiente"}</td>
                <td>
                  <div>
                    <span className="badge">{conversation.salesMemory.stage || "idle"}</span>
                  </div>
                  <div className="subtitle">{conversation.salesMemory.selectedProductName || "Sin producto seleccionado"}</div>
                  {conversation.salesMemory.lastOrderId ? (
                    <div className="subtitle">Pedido #{conversation.salesMemory.lastOrderId.slice(0, 8)}</div>
                  ) : null}
                </td>
                <td>{conversation.messages[0]?.content || "Sin mensajes"}</td>
                <td>
                  <span className={conversation.status === "needs_human" ? "badge warn" : "badge"}>
                    {conversation.status}
                  </span>
                  <br />
                  <Link className="btn secondary" style={{ marginTop: 8 }} href={`/admin/conversations/${conversation.id}`}>
                    Abrir
                  </Link>
                </td>
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6}>Aún no hay conversaciones.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
