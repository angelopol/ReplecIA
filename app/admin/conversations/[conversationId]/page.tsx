import { revalidatePath } from "next/cache";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { shortDate } from "@/lib/format";
import {
  adminReplyConversation,
  closeConversation,
  getConversationDetail,
} from "@/lib/services/conversations";

async function replyAction(formData: FormData) {
  "use server";
  const session = await getCurrentSession();
  const conversationId = String(formData.get("conversationId") || "");
  const content = String(formData.get("content") || "");
  await adminReplyConversation({
    businessId: session!.businessId,
    conversationId,
    content,
  });
  revalidatePath(`/admin/conversations/${conversationId}`);
  revalidatePath("/admin/conversations");
}

async function closeAction(formData: FormData) {
  "use server";
  const session = await getCurrentSession();
  const conversationId = String(formData.get("conversationId") || "");
  await closeConversation(session!.businessId, conversationId);
  revalidatePath(`/admin/conversations/${conversationId}`);
  revalidatePath("/admin/conversations");
  redirect(`/admin/conversations/${conversationId}`);
}

export default async function ConversationDetailPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const session = await getCurrentSession();
  const { conversationId } = await params;
  const conversation = await getConversationDetail(session!.businessId, conversationId);

  if (!conversation) {
    notFound();
  }

  const vehicle = [conversation.vehicle.make, conversation.vehicle.model, conversation.vehicle.year, conversation.vehicle.engine]
    .filter(Boolean)
    .join(" ");

  return (
    <AdminShell>
      <div className="topbar">
        <div>
          <Link className="subtitle" href="/admin/conversations">
            ← Conversaciones
          </Link>
          <h1 className="title">Conversación #{conversation.id.slice(0, 8)}</h1>
          <p className="subtitle">
            {vehicle || "Sin vehículo completo"} · estado: {conversation.status}
          </p>
        </div>
        <form action={closeAction}>
          <input type="hidden" name="conversationId" value={conversation.id} />
          <button className="btn secondary" type="submit" disabled={conversation.status === "closed"}>
            Cerrar conversación
          </button>
        </form>
      </div>

      <section className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="card metric">
          Etapa comercial
          <strong style={{ fontSize: "1.2rem" }}>{conversation.salesMemory.stage || "idle"}</strong>
        </div>
        <div className="card metric">
          Producto
          <strong style={{ fontSize: "1rem" }}>
            {conversation.salesMemory.selectedProductName || "Sin selección"}
          </strong>
        </div>
        <div className="card metric">
          Pedido vinculado
          <strong style={{ fontSize: "1rem" }}>
            {conversation.salesMemory.lastOrderId
              ? `#${conversation.salesMemory.lastOrderId.slice(0, 8)}`
              : "Pendiente"}
          </strong>
        </div>
      </section>

      <section className="conversation-detail panel">
        <div className="conversation-thread">
          {conversation.messages.map((message) => (
            <div className={`thread-message thread-message--${message.role}`} key={message.id}>
              <div className="thread-message__meta">
                <strong>{roleLabel(message.role)}</strong>
                <span>{shortDate(message.createdAt)}</span>
              </div>
              <p>{message.content}</p>
            </div>
          ))}
          {conversation.messages.length === 0 ? <p className="subtitle">Sin mensajes.</p> : null}
        </div>

        <form className="admin-reply" action={replyAction}>
          <input type="hidden" name="conversationId" value={conversation.id} />
          <label htmlFor="content">Responder como administrador</label>
          <textarea
            id="content"
            name="content"
            rows={4}
            placeholder="Escribe una respuesta manual para el cliente..."
            disabled={conversation.status === "closed"}
            required
          />
          <button className="btn" type="submit" disabled={conversation.status === "closed"}>
            Enviar respuesta
          </button>
        </form>
      </section>
    </AdminShell>
  );
}

function roleLabel(role: string) {
  if (role === "user") return "Cliente";
  if (role === "assistant") return "ReplecIA";
  if (role === "admin") return "Administrador";
  return "Sistema";
}
