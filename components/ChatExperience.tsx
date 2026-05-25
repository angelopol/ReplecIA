"use client";

import { CreditCard, ImagePlus, Lock, Send, Wrench, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  imagePreview?: string;
};

type CommercialState = {
  stage?: "idle" | "contact" | "delivery" | "payment" | "completed";
  selectedProductId?: string;
  selectedProductName?: string;
  selectedPriceUsd?: string;
  selectedStock?: number;
  checkoutSessionId?: string;
  lastOrderId?: string;
  lastPaymentReference?: string;
  customerPhone?: string;
  customerName?: string;
  deliveryAddress?: string;
};

type CheckoutView = {
  id: string;
  status: string;
  amountUsd: string;
  productName: string;
  quantity: number;
};

type OrderSummary = {
  id: string;
  status: string;
  paymentStatus: string;
  paymentReference?: string;
  deliveryStatus: string;
  deliveryAddress?: string | null;
  customerName?: string;
  customerPhone?: string;
  totalUsd: string;
  items: {
    quantity: number;
    productName: string;
    unitPriceUsd?: string;
  }[];
};

const STORAGE_KEY = "replecia_chat_history_v4";
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const INITIAL_MESSAGE =
  "Hola, soy ReplecIA. Puedo asesorarte, verificar inventario y cerrar tu compra aqui mismo. Dime que repuesto necesitas o que falla presenta el vehiculo.";

export default function ChatExperience() {
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [vehicle, setVehicle] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", content: INITIAL_MESSAGE }]);
  const [commercialState, setCommercialState] = useState<CommercialState>({ stage: "idle" });
  const [checkout, setCheckout] = useState<CheckoutView | null>(null);
  const [orderSummary, setOrderSummary] = useState<OrderSummary | null>(null);
  const [text, setText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isPaying, setIsPaying] = useState(false);
  const [image, setImage] = useState<{ file: File; preview: string } | null>(null);
  const [card, setCard] = useState({ number: "", name: "", expiry: "", cvc: "" });
  const messagesRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved) as {
        conversationId?: string;
        vehicle?: Record<string, string>;
        messages?: ChatMessage[];
        commercialState?: CommercialState;
        checkout?: CheckoutView | null;
        orderSummary?: OrderSummary | null;
      };
      if (parsed.conversationId) setConversationId(parsed.conversationId);
      if (parsed.vehicle) setVehicle(parsed.vehicle);
      if (Array.isArray(parsed.messages) && parsed.messages.length > 0) setMessages(parsed.messages);
      if (parsed.commercialState) setCommercialState(parsed.commercialState);
      if (parsed.checkout) setCheckout(parsed.checkout);
      if (parsed.orderSummary) setOrderSummary(parsed.orderSummary);
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ conversationId, vehicle, messages, commercialState, checkout, orderSummary }),
    );
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: "smooth" });
  }, [conversationId, vehicle, messages, commercialState, checkout, orderSummary]);

  useEffect(() => {
    if (!commercialState.lastOrderId || orderSummary?.id === commercialState.lastOrderId) return;
    let cancelled = false;
    fetch(`/api/orders/${commercialState.lastOrderId}/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data?.order) setOrderSummary(data.order);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [commercialState.lastOrderId, orderSummary?.id]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading) return;

    const message = text.trim();
    const selectedImage = image;
    if (!message && !selectedImage) return;

    const outgoingContent = message || "Imagen del vehiculo adjunta";
    setText("");
    setImage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setMessages((current) => [
      ...current,
      { role: "user", content: outgoingContent, imagePreview: selectedImage?.preview },
    ]);
    setIsLoading(true);

    try {
      const imagePayload = selectedImage ? await fileToPayload(selectedImage.file) : undefined;
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversationId,
          message: outgoingContent,
          vehicle,
          image: imagePayload,
          memory: buildChatMemory(messages, commercialState),
        }),
      });
      const raw = await response.text();
      const data = raw ? JSON.parse(raw) : {};
      if (!response.ok) throw new Error(data.reply || "No se pudo contactar al asesor.");

      setConversationId(data.conversationId);
      setVehicle(data.vehicle || {});
      setCommercialState(data.commercialState || { stage: "idle" });
      setCheckout(data.checkout || null);
      setMessages((current) => [...current, { role: "assistant", content: data.reply }]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: error instanceof Error ? error.message : "No se pudo completar la consulta." },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  async function confirmPayment() {
    if (!checkout) return;
    if (!isCardReady(card)) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "Revisa los datos de la tarjeta para confirmar el pago seguro." },
      ]);
      return;
    }

    setIsPaying(true);
    try {
      const response = await fetch("/api/payments/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkoutSessionId: checkout.id, card }),
      });
      const raw = await response.text();
      const data = raw ? JSON.parse(raw) : {};
      if (!response.ok) throw new Error(data.message || "No se pudo confirmar el pago.");

      const nextState: CommercialState = {
        ...commercialState,
        stage: "completed",
        lastOrderId: data.receipt.orderId,
        lastPaymentReference: data.receipt.reference,
        checkoutSessionId: checkout.id,
      };
      setOrderSummary({
        id: data.receipt.orderId,
        status: data.order.status,
        paymentStatus: data.order.paymentStatus,
        paymentReference: data.receipt.reference,
        deliveryStatus: data.order.deliveryStatus,
        deliveryAddress: data.order.deliveryAddress,
        customerName: data.order.customerName,
        customerPhone: data.order.customerPhone,
        totalUsd: data.order.totalUsd,
        items: [
          {
            quantity: checkout.quantity,
            productName: checkout.productName,
          },
        ],
      });
      setCommercialState(nextState);
      setCheckout(null);
      setCard({ number: "", name: "", expiry: "", cvc: "" });
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: `Pago aprobado. Tu pedido #${String(data.receipt.orderId).slice(0, 8)} quedo confirmado y pasa a coordinacion de entrega. Referencia de pago: ${data.receipt.reference}.`,
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: error instanceof Error ? error.message : "No pude confirmar el pago." },
      ]);
    } finally {
      setIsPaying(false);
    }
  }

  function handleImageChange(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setMessages((current) => [...current, { role: "assistant", content: "Solo puedo recibir imagenes." }]);
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setMessages((current) => [...current, { role: "assistant", content: "La imagen es muy pesada. Usa una foto menor a 4 MB." }]);
      return;
    }
    if (image?.preview) URL.revokeObjectURL(image.preview);
    setImage({ file, preview: URL.createObjectURL(file) });
  }

  function resetChat() {
    setConversationId(undefined);
    setVehicle({});
    setMessages([{ role: "assistant", content: INITIAL_MESSAGE }]);
    setCommercialState({ stage: "idle" });
    setCheckout(null);
    setOrderSummary(null);
    setCard({ number: "", name: "", expiry: "", cvc: "" });
    setImage(null);
    setText("");
    if (fileInputRef.current) fileInputRef.current.value = "";
    window.localStorage.removeItem(STORAGE_KEY);
  }

  return (
    <div className="chat-experience">
    <div className="chat-card">
      <div className="chat-head">
        <div>
          <strong>Asesor IA de autopartes</strong>
          <p className="subtitle" style={{ margin: 0 }}>
            Diagnostico, compatibilidad, inventario y venta en tiempo real.
          </p>
        </div>
        <div className="chat-actions">
          <button className="btn secondary icon-btn" type="button" onClick={resetChat} title="Reiniciar conversacion">
            Limpiar
          </button>
          <span className="badge">
            <Wrench size={14} /> Activo
          </span>
        </div>
      </div>

      <div className="messages" ref={messagesRef}>
        {messages.map((message, index) => (
          <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
            {message.imagePreview ? <img className="message-image" src={message.imagePreview} alt="Imagen enviada" /> : null}
            {message.content}
          </div>
        ))}
        {isLoading ? <TypingIndicator /> : null}
      </div>

      {image ? (
        <div className="attachment-preview">
          <img src={image.preview} alt="Imagen lista para enviar" />
          <span>{image.file.name}</span>
          <button type="button" onClick={() => setImage(null)} aria-label="Quitar imagen">
            <X size={16} />
          </button>
        </div>
      ) : null}

      {checkout ? (
        <div className="payment-gateway">
          <div className="payment-gateway__head">
            <div>
              <strong>Pago seguro</strong>
              <span>
                {checkout.productName} · ${checkout.amountUsd}
              </span>
            </div>
            <span className="secure-pill">
              <Lock size={13} /> Cifrado
            </span>
          </div>
          <div className="card-preview">
            <CreditCard size={22} />
            <span>{card.number ? maskCard(card.number) : "•••• •••• •••• ••••"}</span>
          </div>
          <div className="payment-fields">
            <input value={card.number} onChange={(event) => setCard({ ...card, number: formatCardNumber(event.target.value) })} placeholder="Numero de tarjeta" inputMode="numeric" maxLength={19} />
            <input value={card.name} onChange={(event) => setCard({ ...card, name: event.target.value })} placeholder="Nombre en la tarjeta" />
            <input value={card.expiry} onChange={(event) => setCard({ ...card, expiry: formatExpiry(event.target.value) })} placeholder="MM/AA" inputMode="numeric" maxLength={5} />
            <input value={card.cvc} onChange={(event) => setCard({ ...card, cvc: event.target.value.replace(/\D/g, "").slice(0, 4) })} placeholder="CVC" inputMode="numeric" maxLength={4} />
          </div>
          <button className="btn orange" type="button" onClick={confirmPayment} disabled={isPaying}>
            {isPaying ? "Procesando..." : `Pagar $${checkout.amountUsd}`}
          </button>
        </div>
      ) : null}

      <form className="chat-form" onSubmit={submit}>
        <input ref={fileInputRef} className="sr-only" type="file" accept="image/*" onChange={(event) => handleImageChange(event.target.files?.[0])} />
        <button className="btn secondary attach-btn" type="button" onClick={() => fileInputRef.current?.click()} title="Adjuntar foto">
          <ImagePlus size={18} />
        </button>
        <input name="message" value={text} onChange={(event) => setText(event.target.value)} placeholder={placeholderForStage(commercialState.stage)} autoComplete="off" />
        <button className="btn" type="submit" disabled={isLoading}>
          <Send size={16} />
          Enviar
        </button>
      </form>
    </div>
    <PurchasePanel order={orderSummary} checkout={checkout} />
    </div>
  );
}

function buildChatMemory(messages: ChatMessage[], commercialState: CommercialState) {
  return {
    ...commercialState,
    recentSummary: messages
      .slice(-8)
      .map((message) => `${message.role}: ${message.content}`)
      .join("\n"),
  };
}

function placeholderForStage(stage?: CommercialState["stage"]) {
  if (stage === "contact") return "Nombre y telefono para reservar la pieza";
  if (stage === "delivery") return "Direccion de delivery o escribe retiro en tienda";
  if (stage === "payment") return "Completa el pago seguro o pregunta por tu pedido";
  return "Ej: mi Corolla 2012 no frena bien, que pastillas sirven?";
}

function formatCardNumber(value: string) {
  return value.replace(/\D/g, "").slice(0, 16).replace(/(.{4})/g, "$1 ").trim();
}

function formatExpiry(value: string) {
  const raw = value.replace(/\D/g, "").slice(0, 4);
  return raw.length <= 2 ? raw : `${raw.slice(0, 2)}/${raw.slice(2)}`;
}

function maskCard(value: string) {
  const raw = value.replace(/\D/g, "");
  return raw.length < 4 ? "•••• •••• •••• ••••" : `•••• •••• •••• ${raw.slice(-4)}`;
}

function isCardReady(card: { number: string; name: string; expiry: string; cvc: string }) {
  return card.number.replace(/\D/g, "").length >= 13 && card.name.trim().length >= 3 && /^\d{2}\/\d{2}$/.test(card.expiry) && card.cvc.length >= 3;
}

function fileToPayload(file: File): Promise<{ mimeType: string; data: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("No pude leer la imagen adjunta."));
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve({ mimeType: file.type, data: result.includes(",") ? result.split(",")[1] : result });
    };
    reader.readAsDataURL(file);
  });
}

function TypingIndicator() {
  return (
    <div className="message assistant typing-indicator" aria-label="ReplecIA esta escribiendo">
      <span />
      <span />
      <span />
    </div>
  );
}

function PurchasePanel({ order, checkout }: { order: OrderSummary | null; checkout: CheckoutView | null }) {
  if (!order && !checkout) {
    return (
      <aside className="purchase-panel purchase-panel--empty">
        <div>
          <span className="badge">Compra</span>
          <h3>Tu compra aparecerá aquí</h3>
          <p>Cuando el asistente reserve una pieza o confirmes el pago, verás el resumen, entrega y referencia fuera del chat.</p>
        </div>
      </aside>
    );
  }

  if (checkout && !order) {
    return (
      <aside className="purchase-panel">
        <span className="badge warn">Pago pendiente</span>
        <h3>Resumen de compra</h3>
        <div className="purchase-line">
          <span>Producto</span>
          <strong>{checkout.productName}</strong>
        </div>
        <div className="purchase-line">
          <span>Cantidad</span>
          <strong>{checkout.quantity}</strong>
        </div>
        <div className="purchase-total">
          <span>Total</span>
          <strong>${checkout.amountUsd}</strong>
        </div>
        <p>Completa el pago seguro en el chat para confirmar el pedido y pasar a coordinación de entrega.</p>
      </aside>
    );
  }

  if (!order) return null;

  return (
    <aside className="purchase-panel purchase-panel--confirmed">
      <span className="badge ok">Pedido confirmado</span>
      <h3>Pedido #{order.id.slice(0, 8)}</h3>
      <div className="purchase-items">
        {order.items.map((item, index) => (
          <div className="purchase-line" key={`${item.productName}-${index}`}>
            <span>{item.quantity} x</span>
            <strong>{item.productName}</strong>
          </div>
        ))}
      </div>
      <div className="purchase-total">
        <span>Total pagado</span>
        <strong>${order.totalUsd}</strong>
      </div>
      <div className="purchase-line">
        <span>Pago</span>
        <strong>{order.paymentReference || order.paymentStatus}</strong>
      </div>
      <div className="purchase-line">
        <span>Entrega</span>
        <strong>{formatDeliveryLabel(order.deliveryStatus)}</strong>
      </div>
      {order.deliveryAddress ? (
        <div className="purchase-line">
          <span>Dirección</span>
          <strong>{order.deliveryAddress}</strong>
        </div>
      ) : null}
      {order.customerPhone ? (
        <div className="purchase-line">
          <span>Contacto</span>
          <strong>{order.customerPhone}</strong>
        </div>
      ) : null}
      <p>La tienda usará estos datos para coordinar la entrega. Puedes preguntar por el estado del pedido en cualquier momento.</p>
    </aside>
  );
}

function formatDeliveryLabel(status: string) {
  const labels: Record<string, string> = {
    pending_coordination: "Pendiente de coordinación",
    coordinated: "Coordinada",
    on_the_way: "En camino",
    delivered: "Entregada",
  };
  return labels[status] || status;
}
