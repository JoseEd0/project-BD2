import { useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { useCustomerRecommendations, useProduct, useProductRecommendations } from "../api/shop";
import type { Product, Recommendations } from "../api/types";
import { Icon } from "../components/Icon";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProductPhoto } from "../components/ProductPhoto";
import { Stars } from "../components/Stars";
import { RecommendationRails } from "../components/RecommendationRails";
import { useCustomer } from "../customer/CustomerContext";
import { formatPrice } from "../format";
import { APP } from "../identity";
import { useShopperLocation } from "../location/LocationContext";
import { categoryTrail, descriptionParts } from "../product/description";
import { PurchaseBox } from "../product/PurchaseBox";
import { StockPanel } from "../product/StockPanel";
import { newlyRecommended } from "../recommend/rails";
import { ChangedNotice } from "../components/ChangedNotice";

function ProductSheet({ product }: { readonly product: Product }) {
  const price = formatPrice(product.precio);
  const trail = categoryTrail(product.ruta_categoria);
  return (
    <div className="sheet">
      {trail.length > 0 && (
        <p className="sheet-trail" lang="en">
          {trail.join(" › ")}
        </p>
      )}
      <h1 className="sheet-title" lang="en">
        {product.titulo ?? "Producto sin título"}
      </h1>
      {product.marca && (
        <p className="sheet-brand">
          Marca: <strong>{product.marca}</strong>
        </p>
      )}
      <Stars rating={product.valoracion} count={product.valoraciones} />
      <p className={price === null ? "sheet-price sheet-price-missing" : "sheet-price"}>{price ?? "Precio no disponible"}</p>
      <p className="sheet-id">
        N.º de catálogo <span className="code">{product.id}</span>
      </p>
    </div>
  );
}

function Description({ text }: { readonly text: string | null }) {
  const { bullets, paragraphs } = descriptionParts(text);
  if (bullets.length === 0 && paragraphs.length === 0) {
    return null;
  }
  return (
    <details className="description">
      <summary>
        <Icon name="right" size={18} />
        Descripción de la ficha (en inglés)
      </summary>
      <div lang="en">
        {bullets.length > 0 && (
          <ul>
            {bullets.map((bullet, index) => (
              <li key={index}>{bullet}</li>
            ))}
          </ul>
        )}
        {paragraphs.map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
    </details>
  );
}

/** La ficha: el producto, comprarlo, dónde hay stock y las recomendaciones de cada señal. */
export default function ProductPage() {
  const { id = "" } = useParams();
  const { customer } = useCustomer();
  const { point } = useShopperLocation();
  const product = useProduct(id);
  const recommendations = useProductRecommendations(id, customer, point);
  const forCustomer = useCustomerRecommendations(customer, point);
  const before = useRef<{ product: Recommendations | undefined; customer: Recommendations | undefined }>({
    product: undefined,
    customer: undefined,
  });
  const [purchased, setPurchased] = useState(false);

  if (product.isError) {
    return (
      <div className="page-problem">
        <ProblemNotice
          title={product.error.title}
          detail={product.error.detail}
          onRetry={product.error.retryable ? () => void product.refetch() : undefined}
        />
        <Link to="/">Volver al catálogo</Link>
      </div>
    );
  }
  if (product.isPending) {
    return (
      <p className="page-loading" role="status">
        Cargando el producto…
      </p>
    );
  }
  const item = product.data;
  const snapshot = () => {
    before.current = { product: recommendations.data?.data, customer: forCustomer.data?.data };
    setPurchased(false);
  };
  const newForProduct = purchased ? newlyRecommended(before.current.product, recommendations.data?.data) : new Set<string>();
  const newForCustomer = purchased ? newlyRecommended(before.current.customer, forCustomer.data?.data) : new Set<string>();

  return (
    <article className="product">
      <title>{`${item.titulo ?? item.id} · ${APP.name}`}</title>
      <div className="product-top">
        <div className="product-main">
          <div className="product-photo">
            <ProductPhoto productId={item.id} hasPhoto={item.imagen !== null} eager />
          </div>
          <ProductSheet product={item} />
          <Description text={item.descripcion} />
        </div>
        <div className="product-side">
          <PurchaseBox product={item} onBeforePurchase={snapshot} onPurchased={() => setPurchased(true)} />
          <StockPanel productId={item.id} />
        </div>
      </div>
      {purchased && forCustomer.data !== undefined && (
        <ChangedNotice
          fetching={forCustomer.isFetching}
          changed={newForCustomer.size}
          cache={forCustomer.data.cache}
          result={forCustomer.data}
          newIds={newForCustomer}
        />
      )}
      <section className="product-recommendations" aria-labelledby="recommendations-title">
        <h2 id="recommendations-title" className="section-title">
          Por qué te recomendamos esto
        </h2>
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
          <RecommendationRails
            result={recommendations.data}
            scope="producto"
            fusedTitle="Lo que más va con este producto"
            newIds={newForProduct}
          />
        )}
      </section>
    </article>
  );
}
