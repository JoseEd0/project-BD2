import { useId } from "react";

import { useCustomers } from "../api/shop";
import { useCustomer } from "../customer/CustomerContext";
import { formatCount, shortId } from "../format";
import { Icon } from "./Icon";

const NO_CUSTOMER = "";

/** «Cliente 3 · 210 compras · AH4GZF…WGZYA»: los ids de Amazon no dicen nada, el puesto sí. */
export function customerLabel(id: string, purchases: number, rank: number): string {
  return rank < 0 ? `Cliente ${shortId(id)}` : `Cliente ${rank + 1} · ${formatCount(purchases)} compras · ${shortId(id)}`;
}

/** Para quién se recomienda y quién compra: uno de los clientes del dataset con más compras. */
export function CustomerPicker() {
  const { customer, chooseCustomer } = useCustomer();
  const customers = useCustomers();
  const id = useId();
  const known = customers.data ?? [];
  const listed = customer === null || known.some((item) => item.id === customer) ? known : [{ id: customer, compras: 0 }, ...known];
  return (
    <div className="customer">
      <label htmlFor={id} className="customer-label">
        <Icon name="user" size={18} />
        <span>Cliente</span>
      </label>
      <select
        id={id}
        className="customer-select"
        value={customer ?? NO_CUSTOMER}
        onChange={(event) => chooseCustomer(event.target.value === NO_CUSTOMER ? null : event.target.value)}
        disabled={customers.isPending && customer === null}
      >
        <option value={NO_CUSTOMER}>{customers.isPending ? "Cargando clientes…" : "Sin cliente"}</option>
        {listed.map((item) => (
          <option key={item.id} value={item.id}>
            {customerLabel(item.id, item.compras, known.findIndex((other) => other.id === item.id))}
          </option>
        ))}
      </select>
    </div>
  );
}
