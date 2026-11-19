import { useCustomerPurchases, useCustomerRecommendations, useCustomers } from "../api/shop";
import { customerLabel } from "../components/CustomerPicker";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProductTile } from "../components/ProductTile";
import { Rail } from "../components/Rail";
import { RecommendationRails } from "../components/RecommendationRails";
import { useCustomer } from "../customer/CustomerContext";
import { APP } from "../identity";
import { useShopperLocation } from "../location/LocationContext";

function ChooseCustomer() {
  const customers = useCustomers();
  const { chooseCustomer } = useCustomer();
  return (
    <section className="choose" aria-labelledby="choose-title">
      <h1 id="choose-title">Recomendado para ti</h1>
      <p className="lede">
        Elige un cliente: son clientes reales del dataset de Amazon, los que más compraron. Sus compras de entrenamiento
        son lo que la tienda sabe de ellos.
      </p>
      {customers.isError ? (
        <ProblemNotice title={customers.error.title} detail={customers.error.detail} onRetry={() => void customers.refetch()} />
      ) : customers.isPending ? (
        <p className="muted" role="status">
          Cargando clientes…
        </p>
      ) : (
        <ul className="choose-list">
          {customers.data.map((item, index) => (
            <li key={item.id}>
              <button type="button" className="choose-customer" onClick={() => chooseCustomer(item.id)}>
                {customerLabel(item.id, item.compras, index)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Lo que la tienda le recomienda al cliente elegido, señal por señal, y lo que ya compró. */
export default function ForYouPage() {
  const { customer } = useCustomer();
  const { point } = useShopperLocation();
  const purchases = useCustomerPurchases(customer);
  const recommendations = useCustomerRecommendations(customer, point);
  if (customer === null) {
    return (
      <>
        <title>{`Para ti · ${APP.name}`}</title>
        <ChooseCustomer />
      </>
    );
  }
  const bought = purchases.data ?? [];
  return (
    <div className="for-you">
      <title>{`Para ti · ${APP.name}`}</title>
      <h1 className="section-title">Recomendado para ti</h1>
      <p className="lede">
        A partir de tus últimas compras: lo que otros compraron con ellas, lo que se les parece en la foto y en la
        descripción, y lo que hay en stock cerca.
      </p>
      {purchases.isError ? (
        <ProblemNotice title={purchases.error.title} detail={purchases.error.detail} onRetry={() => void purchases.refetch()} />
      ) : (
        <Rail
          title="Lo que compraste"
          meta="Las más recientes primero; las recomendaciones las dejan fuera."
          emptyText={purchases.isPending ? "Cargando tus compras…" : "Este cliente aún no compró nada."}
          count={bought.length}
        >
          {bought.map((product) => (
            <li key={product.id}>
              <ProductTile product={product} />
            </li>
          ))}
        </Rail>
      )}
      {recommendations.isError ? (
        <ProblemNotice
          title={recommendations.error.title}
          detail={recommendations.error.detail}
          onRetry={() => void recommendations.refetch()}
        />
      ) : recommendations.data === undefined ? (
        <p className="muted" role="status">
          Consultando las cuatro señales…
        </p>
      ) : (
        <RecommendationRails result={recommendations.data} scope="usuario" fusedTitle="Lo que más va contigo" />
      )}
    </div>
  );
}
