import { usePurchase } from "../api/shop";
import type { Product, PurchaseRecorded } from "../api/types";
import { Icon } from "../components/Icon";
import { useCustomer } from "../customer/CustomerContext";
import { formatCount, shortId } from "../format";

const CUSTOMER_PICKER = ".customer-select";

interface PurchaseBoxProps {
  readonly product: Product;
  readonly onPurchased: (recorded: PurchaseRecorded) => void;
  readonly onBeforePurchase: () => void;
}

function focusCustomerPicker(): void {
  document.querySelector<HTMLSelectElement>(CUSTOMER_PICKER)?.focus();
}

function pairsText(recorded: PurchaseRecorded): string {
  if (recorded.pares_nuevos === 0 && recorded.pares_sumados === 0) {
    return "Ya estaba entre tus compras, así que las compras conjuntas no cambiaron.";
  }
  const added = recorded.pares_nuevos === 1 ? "1 par nuevo" : `${formatCount(recorded.pares_nuevos)} pares nuevos`;
  const incremented = recorded.pares_sumados === 1 ? "1 par sumó un cliente" : `${formatCount(recorded.pares_sumados)} pares sumaron un cliente`;
  return `${added} y ${incremented} en las compras conjuntas.`;
}

/** Comprar, aparte de todo lo demás: es lo único que cambia datos. La compra es simulada. */
export function PurchaseBox({ product, onPurchased, onBeforePurchase }: PurchaseBoxProps) {
  const { customer } = useCustomer();
  const purchase = usePurchase();
  if (customer === null) {
    return (
      <div className="purchase">
        <p>Para comprar, elige primero un cliente: la compra se registra a su nombre y cambia lo que se le recomienda.</p>
        <button type="button" className="button button-quiet" onClick={focusCustomerPicker}>
          <Icon name="user" size={18} />
          Elegir cliente
        </button>
      </div>
    );
  }
  const buy = () => {
    onBeforePurchase();
    purchase.mutate({ customer, product: product.id }, { onSuccess: onPurchased });
  };
  return (
    <div className="purchase">
      <button type="button" className="button button-buy" onClick={buy} disabled={purchase.isPending}>
        <Icon name="bag" />
        {purchase.isPending ? "Registrando la compra…" : "Comprar"}
      </button>
      <p className="purchase-note">
        Compra simulada a nombre de {shortId(customer)}: se guarda en el gestor y la caché de sus recomendaciones se
        invalida.
      </p>
      <div aria-live="polite">
        {purchase.isSuccess && (
          <p className="purchase-done">
            <Icon name="check" size={18} />
            <span>
              Compra n.º {formatCount(purchase.data.id)} registrada. {pairsText(purchase.data)}
            </span>
          </p>
        )}
        {purchase.isError && (
          <p className="field-error" role="alert">
            {purchase.error.title}. {purchase.error.detail}
          </p>
        )}
      </div>
    </div>
  );
}
