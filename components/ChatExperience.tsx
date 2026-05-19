"use client";

import { Send, Wrench } from "lucide-react";
import { useState, useTransition } from "react";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export default function ChatExperience() {
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [vehicle, setVehicle] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: "assistant",
      content:
        "Hola, soy ReplecIA. Cuéntame qué falla presenta el vehículo o qué repuesto necesitas, y reviso compatibilidad con inventario.",
    },
  ]);
  const [text, setText] = useState("");
  const [isPending, startTransition] = useTransition();

  function submit(formData: FormData) {
    const message = String(formData.get("message") || "").trim();
    if (!message) return;

    setText("");
    setMessages((current) => [...current, { role: "user", content: message }]);

    startTransition(async () => {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId, message, vehicle }),
      });
      const data = await response.json();
      setConversationId(data.conversationId);
      setVehicle(data.vehicle || {});
      setMessages((current) => [...current, { role: "assistant", content: data.reply }]);
    });
  }

  return (
    <div className="chat-card">
      <div className="chat-head">
        <div>
          <strong>Asesor IA de autopartes</strong>
          <p className="subtitle" style={{ margin: 0 }}>
            Diagnóstico, compatibilidad e inventario en tiempo real.
          </p>
        </div>
        <span className="badge">
          <Wrench size={14} /> Activo
        </span>
      </div>
      <div className="messages">
        {messages.map((message, index) => (
          <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
            {message.content}
          </div>
        ))}
        {isPending ? <div className="message assistant">Revisando compatibilidad...</div> : null}
      </div>
      <form className="chat-form" action={submit}>
        <input
          name="message"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Ej: mi Corolla 2012 no frena bien, ¿qué pastillas sirven?"
          autoComplete="off"
        />
        <button className="btn" type="submit" disabled={isPending}>
          <Send size={16} />
          Enviar
        </button>
      </form>
    </div>
  );
}
