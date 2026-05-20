import { revalidatePath } from "next/cache";
import AdminShell from "@/components/AdminShell";
import { getCurrentSession } from "@/lib/auth";
import { createInventoryItem, listInventory, updateInventoryItem } from "@/lib/services/inventory";
import { moneyUsd } from "@/lib/format";

async function createProductAction(formData: FormData) {
  "use server";
  const session = await getCurrentSession();
  await createInventoryItem(session!.businessId, formData);
  revalidatePath("/admin/inventory");
}

async function updateProductAction(formData: FormData) {
  "use server";
  const productId = String(formData.get("productId") || "");
  await updateInventoryItem(productId, formData);
  revalidatePath("/admin/inventory");
}

export default async function InventoryPage() {
  const session = await getCurrentSession();
  const inventory = await listInventory(session!.businessId);

  return (
    <AdminShell>
      <div className="topbar">
        <div>
          <h1 className="title">Inventario</h1>
          <p className="subtitle">Repuestos, stock y compatibilidad vehicular.</p>
        </div>
      </div>

      <form className="card form-grid" action={createProductAction}>
        <div className="field">
          <label>SKU</label>
          <input name="sku" required />
        </div>
        <div className="field">
          <label>Nombre del repuesto</label>
          <input name="name" required />
        </div>
        <div className="field">
          <label>Marca</label>
          <input name="brand" />
        </div>
        <div className="field">
          <label>Categoría</label>
          <input name="category" placeholder="Frenos, suspensión, motor..." />
        </div>
        <div className="field">
          <label>Precio USD</label>
          <input name="priceUsd" type="number" min="0" step="0.01" defaultValue="0" />
        </div>
        <div className="field">
          <label>Stock</label>
          <input name="stock" type="number" min="0" step="1" defaultValue="1" />
        </div>
        <div className="field full">
          <label>Descripción técnica</label>
          <textarea name="description" rows={3} />
        </div>
        <div className="field">
          <label>Compatible con marca</label>
          <input name="make" placeholder="Toyota" />
        </div>
        <div className="field">
          <label>Modelo</label>
          <input name="model" placeholder="Corolla" />
        </div>
        <div className="field">
          <label>Año desde</label>
          <input name="yearFrom" type="number" min="1900" max="2100" />
        </div>
        <div className="field">
          <label>Año hasta</label>
          <input name="yearTo" type="number" min="1900" max="2100" />
        </div>
        <div className="field full">
          <label>Motor</label>
          <input name="engine" placeholder="1.8, 2.0, diesel..." />
        </div>
        <div className="full">
          <button className="btn orange" type="submit">
            Agregar repuesto
          </button>
        </div>
      </form>

      <section className="panel" style={{ marginTop: 16 }}>
        <table className="table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Repuesto</th>
              <th>Compatibilidad</th>
              <th>Stock</th>
              <th>Precio</th>
              <th>Edición rápida</th>
            </tr>
          </thead>
          <tbody>
            {inventory.map((item) => (
              <tr key={item.id}>
                <td>{item.sku}</td>
                <td>
                  <strong>{item.name}</strong>
                  <br />
                  <span className="subtitle">{item.brand || "Sin marca"} · {item.category?.name || "Sin categoría"}</span>
                </td>
                <td>
                  {item.compatibilities.length
                    ? item.compatibilities.map((compat) => `${compat.make} ${compat.model} ${compat.yearFrom || ""}-${compat.yearTo || ""}`).join(", ")
                    : "Pendiente"}
                </td>
                <td>
                  <span className={item.stock > 0 ? "badge ok" : "badge warn"}>{item.stock}</span>
                </td>
                <td>{moneyUsd(item.priceUsd)}</td>
                <td>
                  <form action={updateProductAction} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <input type="hidden" name="productId" value={item.id} />
                    <input name="stock" type="number" min="0" defaultValue={item.stock} style={{ width: 82 }} />
                    <input name="priceUsd" type="number" min="0" step="0.01" defaultValue={item.priceUsd} style={{ width: 100 }} />
                    <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <input name="active" type="checkbox" defaultChecked={item.active} />
                      Activo
                    </label>
                    <button className="btn secondary" type="submit">
                      Guardar
                    </button>
                  </form>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
