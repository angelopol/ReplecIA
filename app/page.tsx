import Link from "next/link";
import ChatExperience from "@/components/ChatExperience";

export default function HomePage() {
  return (
    <main className="chat-page">
      <section className="chat-hero">
        <div>
          <div className="brand">ReplecIA</div>
          <h1>El motor digital de tu tienda de repuestos</h1>
          <p>
            Un asistente web potenciado con IA para diagnosticar necesidades, validar compatibilidad vehicular y
            convertir consultas en pedidos trazables.
          </p>
        </div>
        <div className="grid">
          <Link className="btn orange" href="/admin">
            Panel administrativo
          </Link>
          <span style={{ color: "rgba(255,255,255,.72)" }}>Tu inventario conectado. Tu cliente asesorado.</span>
        </div>
      </section>
      <section className="chat-wrap">
        <ChatExperience />
      </section>
    </main>
  );
}
