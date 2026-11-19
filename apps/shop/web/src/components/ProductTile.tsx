import { Link } from "react-router";

import type { ProductSummary } from "../api/types";
import type { Signal } from "../recommend/signals";
import { HangTag } from "./HangTag";
import { ProductPhoto } from "./ProductPhoto";
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

/** Un producto en una lista: su foto y su etiqueta colgada, todo un enlace a la ficha. */
export function ProductTile({ product, reason, isNew = false, score }: ProductTileProps) {
  return (
    <Link to={`/producto/${encodeURIComponent(product.id)}`} className={isNew ? "tile tile-new" : "tile"}>
      <ProductPhoto productId={product.id} hasPhoto={product.imagen !== null} />
      <span className="tile-string" aria-hidden="true" />
      <HangTag product={product}>
        {score !== undefined && <span className="tag-score">{score}</span>}
        {(reason !== undefined || isNew) && (
          <span className="tag-reason">
            <span className="stamps">
              {isNew && <NewStamp />}
              {reason?.stamps.map((signal) => <Stamp key={signal} signal={signal} />)}
            </span>
            {reason !== undefined && <span className="tag-reason-text">{reason.text}</span>}
          </span>
        )}
      </HangTag>
    </Link>
  );
}
