import { Link } from "react-router";

import type { ScoredProduct } from "../api/types";
import { ProductPhoto } from "../components/ProductPhoto";
import { formatPrice } from "../format";

interface SearchPreviewProps {
  readonly id: string;
  readonly products: readonly ScoredProduct[];
  readonly correctedQuery: string | null;
  readonly activeIndex: number;
  readonly loading: boolean;
  readonly failed: boolean;
  readonly onPick: () => void;
  readonly onSeeAll: () => void;
}

export function previewOptionId(listId: string, index: number): string {
  return `${listId}-${index}`;
}

/** La lista bajo el buscador: los primeros productos con su foto, título y precio. */
export function SearchPreview({ id, products, correctedQuery, activeIndex, loading, failed, onPick, onSeeAll }: SearchPreviewProps) {
  return (
    <div className="preview" role="presentation">
      {correctedQuery !== null && (
        <p className="preview-correction">
          Resultados de <strong lang="en">{correctedQuery}</strong>
        </p>
      )}
      {failed ? (
        <p className="preview-status">No pudimos cargar la vista previa. Pulsa Enter para buscar.</p>
      ) : products.length === 0 ? (
        <p className="preview-status" role="status">
          {loading ? "Buscando…" : "Sin coincidencias. Pulsa Enter para buscar igual."}
        </p>
      ) : (
        <ul id={id} role="listbox" aria-label="Productos sugeridos" className="preview-list">
          {products.map(({ producto }, index) => (
            <li key={producto.id} role="presentation">
              <Link
                id={previewOptionId(id, index)}
                role="option"
                aria-selected={index === activeIndex}
                tabIndex={-1}
                className="preview-item"
                to={`/producto/${encodeURIComponent(producto.id)}`}
                onClick={onPick}
              >
                <ProductPhoto productId={producto.id} hasPhoto={producto.imagen !== null} eager />
                <span className="preview-title" lang="en">
                  {producto.titulo ?? "Producto sin título"}
                </span>
                <span className="preview-price">{formatPrice(producto.precio) ?? "Sin precio"}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="preview-all" onMouseDown={(event) => event.preventDefault()} onClick={onSeeAll}>
        Ver todos los resultados
      </button>
    </div>
  );
}
