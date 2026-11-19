import { Link } from "react-router";

import { useCustomerRecommendations } from "../api/shop";
import { useCustomer } from "../customer/CustomerContext";
import { useShopperLocation } from "../location/LocationContext";
import { stampsOf } from "../recommend/rails";
import { ProblemNotice } from "./ProblemNotice";
import { ProductTile } from "./ProductTile";
import { Rail } from "./Rail";

const PREVIEW = 12;

/** Las primeras recomendaciones del cliente elegido, en la portada del catálogo. */
export function ForYouPreview() {
  const { customer } = useCustomer();
  const { point } = useShopperLocation();
  const recommendations = useCustomerRecommendations(customer, point);
  if (customer === null) {
    return (
      <section className="invite" aria-labelledby="invite-title">
        <h2 id="invite-title">Recomendado para ti</h2>
        <p>
          Elige un cliente en la barra de arriba (son clientes reales del dataset, con sus compras) y aquí verás lo que la
          tienda le recomienda y por qué.
        </p>
      </section>
    );
  }
  if (recommendations.isError) {
    return (
      <ProblemNotice
        title={recommendations.error.title}
        detail={recommendations.error.detail}
        onRetry={() => void recommendations.refetch()}
      />
    );
  }
  const items = recommendations.data?.data.recomendaciones.slice(0, PREVIEW) ?? [];
  return (
    <Rail
      variant="fused"
      title="Recomendado para ti"
      meta={recommendations.isPending ? "Calculando…" : "A partir de tus compras recientes."}
      aside={
        <Link className="button button-link" to="/para-ti">
          Ver por qué, señal por señal
        </Link>
      }
      emptyText={recommendations.isPending ? "Calculando tus recomendaciones…" : "Este cliente no tiene compras con que recomendarle."}
      count={items.length}
    >
      {items.map((item) => (
        <li key={item.producto.id}>
          <ProductTile product={item.producto} reason={{ stamps: stampsOf(item), text: item.explicacion }} />
        </li>
      ))}
    </Rail>
  );
}
