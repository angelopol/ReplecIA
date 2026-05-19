import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { shortDate } from "@/lib/format";
import { listConversations } from "@/lib/services/conversations";

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
              <th>Último mensaje</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((conversation) => (
              <tr key={conversation.id}>
                <td>
                  <strong>{conversation.id.slice(0, 8)}</strong>
                  <br />
                  <span className="subtitle">{shortDate(conversation.updatedAt)}</span>
                </td>
                <td>
                  {[conversation.vehicle.make, conversation.vehicle.model, conversation.vehicle.year, conversation.vehicle.engine]
                    .filter(Boolean)
                    .join(" ") || "Sin datos completos"}
                </td>
                <td>{conversation.diagnosis.recommendation || conversation.diagnosis.symptom || "Pendiente"}</td>
                <td>{conversation.messages[0]?.content || "Sin mensajes"}</td>
                <td>
                  <span className={conversation.status === "needs_human" ? "badge warn" : "badge"}>
                    {conversation.status}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5}>Aún no hay conversaciones.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
