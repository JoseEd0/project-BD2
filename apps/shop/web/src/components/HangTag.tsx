import type { ReactNode } from "react";

import type { ProductSummary } from "../api/types";
import { formatPrice, formatRating } from "../format";

interface HangTagProps {
  readonly product: ProductSummary;
  /** Los sellos y la frase de una recomendación, o lo que la lista quiera colgar. */
  readonly children?: ReactNode;
}

/** La etiqueta manila que cuelga de cada producto: precio sellado, título y marca. */
export function HangTag({ product, children }: HangTagProps) {
  const price = formatPrice(product.precio);
  const rating = formatRating(product.valoracion, product.valoraciones);
  return (
    <span className="tag">
      <span className="tag-eyelet" aria-hidden="true" />
      <span className={price === null ? "tag-price tag-price-missing" : "tag-price"}>{price ?? "Sin precio"}</span>
      <span className="tag-title">{product.titulo ?? "Producto sin título"}</span>
      <span className="tag-meta">
        {product.marca && <span>{product.marca}</span>}
        {rating && <span>{rating}</span>}
      </span>
      {children}
    </span>
  );
}
