import Link from "next/link";
import ChatExperience from "@/components/ChatExperience";
import RepleciaLogo from "@/components/brand/RepleciaLogo";

export default function HomePage() {
  return (
    <main className="landing-page">
      <section className="landing-hero">
        <nav className="landing-nav">
          <RepleciaLogo className="landing-logo" />
          <div>
            <a href="#plataforma">Plataforma</a>
            <a href="#valor">Valor</a>
            <a href="#chat">Demo</a>
            <Link className="btn secondary" href="/admin">
              Panel
            </Link>
          </div>
        </nav>
        <div className="landing-hero__content">
          <p className="eyebrow">SaaS B2B para tiendas de autopartes</p>
          <h1>ReplecIA</h1>
          <p>
            El motor digital de tu tienda de repuestos: inventario conectado, asesoría con IA y ventas guiadas hasta
            el pago y la coordinación de entrega.
          </p>
          <div className="hero-actions">
            <a className="btn orange" href="#chat">
              Probar asistente
            </a>
            <Link className="btn secondary" href="/admin">
              Entrar al panel
            </Link>
          </div>
        </div>
        <div className="hero-proof">
          <span>Diagnóstico</span>
          <span>Compatibilidad</span>
          <span>Inventario</span>
          <span>Pago</span>
        </div>
      </section>

      <section id="valor" className="landing-band">
        <div className="landing-section-head">
          <p className="eyebrow">Tu inventario conectado. Tu cliente asesorado.</p>
          <h2>Diseñada para resolver los dolores reales de una tienda tradicional</h2>
        </div>
        <div className="value-grid">
          <article>
            <strong>Ventas fuera de horario</strong>
            <p>El asistente atiende consultas, valida stock y guía al cliente aunque la tienda esté cerrada.</p>
          </article>
          <article>
            <strong>Menos devoluciones</strong>
            <p>La recomendación se apoya en marca, modelo, año, motor, pieza visible y compatibilidad registrada.</p>
          </article>
          <article>
            <strong>Operación centralizada</strong>
            <p>Pedidos, pagos, conversaciones, inventario y coordinación de entrega quedan trazables en el panel.</p>
          </article>
        </div>
      </section>

      <section id="plataforma" className="platform-section">
        <div>
          <p className="eyebrow">Ecosistema todo en uno</p>
          <h2>Un vendedor técnico que no descansa</h2>
          <p>
            ReplecIA combina asesoría automotriz, memoria conversacional, inventario y flujo comercial para convertir
            dudas técnicas en pedidos listos para coordinar.
          </p>
        </div>
        <div className="platform-steps">
          <span>1. Consulta o foto</span>
          <span>2. Diagnóstico y compatibilidad</span>
          <span>3. Disponibilidad y cierre</span>
          <span>4. Pago y entrega</span>
        </div>
      </section>

      <section className="diagnostic-image-section">
        <div>
          <p className="eyebrow">IA aplicada al mostrador</p>
          <h2>De una foto o síntoma a una oportunidad de venta</h2>
          <p>
            El cliente no siempre conoce el nombre de la pieza. ReplecIA guía la conversación, pide los datos que
            faltan y evita prometer compatibilidad cuando el inventario no lo respalda.
          </p>
        </div>
      </section>

      <section id="chat" className="landing-chat-section">
        <div className="landing-section-head">
          <p className="eyebrow">Demo interactiva</p>
          <h2>Prueba el asesor IA como lo vería un cliente</h2>
        </div>
        <div className="landing-chat-wrap">
          <ChatExperience />
        </div>
      </section>

      <section className="landing-final-cta">
        <div>
          <RepleciaLogo className="final-logo" />
          <h2>Moderniza tu tienda sin dejar de vender como experto</h2>
          <p>
            ReplecIA convierte el inventario en una experiencia comercial guiada: asesoría, pedido, pago y entrega.
          </p>
        </div>
        <Link className="btn orange" href="/admin">
          Ver panel administrativo
        </Link>
      </section>
    </main>
  );
}
