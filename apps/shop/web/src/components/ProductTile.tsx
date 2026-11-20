import { Link } from "react-router";

import type { ProductSummary } from "../api/types";
import { formatPrice } from "../format";
import type { Signal } from "../recommend/signals";
import { ProductPhoto } from "./ProductPhoto";
import { Stars } from "./Stars";
import { NewStamp, Stamp } from "./Stamp";

export interface TileReason {
  readonly stamps: readonly Signal[];
  readonly text: string;
}

interface ProductTileProps {
  readonly product: ProductSummary;
  readonly reason?: TileReason | undefined;
  readonly isNew?: boolean;
  /** El puntaje con que se ordenó un resultado de búsqueda (BM25 o coseno). */
  readonly score?: string | undefined;
}

function Price({ price }: { readonly price: number | null }) {
  const formatted = formatPrice(price);
  if (formatted === null) {
    return <span className="card-price card-price-missing">Precio no disponible</span>;
  }
  const [whole, cents] = formatted.replace("US$ ", "").split(",");
  return (
    <span className="card-price" aria-label={formatted}>
      <span className="card-price-currency">US$</span>
      <span className="card-price-whole">{whole}</span>
      {cents !== undefined && <span className="card-price-cents">{cents}</span>}
    </span>
  );
}

/** Un producto en una lista: foto, marca, título en dos líneas, valoración y precio; todo un enlace a la ficha. */
export function ProductTile({ product, reason, isNew = false, score }: ProductTileProps) {
  return (
    <Link to={`/producto/${encodeURIComponent(product.id)}`} className={isNew ? "tile card tile-new" : "tile card"}>
      <ProductPhoto productId={product.id} hasPhoto={product.imagen !== null} />
      <span className="card-body">
        {product.marca && <span className="card-brand">{product.marca}</span>}
        <span className="card-title" lang="en">
          {product.titulo ?? "Producto sin título"}
        </span>
        <Stars rating={product.valoracion} count={product.valoraciones} />
        <Price price={product.precio} />
        {product.departamento && <span className="card-department">{product.departamento}</span>}
        {(reason !== undefined || isNew) && (
          <span className="tag-reason">
            <span className="stamps">
              {isNew && <NewStamp />}
              {reason?.stamps.map((signal) => <Stamp key={signal} signal={signal} />)}
            </span>
            {reason !== undefined && <span className="tag-reason-text">{reason.text}</span>}
          </span>
        )}
        {score !== undefined && <span className="card-score">{score}</span>}
      </span>
    </Link>
  );
}
