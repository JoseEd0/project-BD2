import type { CacheStatus, Measured, Recommendations } from "../api/types";
import { stampsOf } from "../recommend/rails";
import { ProductTile } from "./ProductTile";
import { Rail } from "./Rail";
import { CACHE_WORDS } from "./StageTimings";

interface ChangedNoticeProps {
  readonly fetching: boolean;
  readonly changed: number;
  readonly cache: CacheStatus | null;
  readonly result: Measured<Recommendations>;
  readonly newIds: ReadonlySet<string>;
}

function summary(changed: number, cache: CacheStatus | null): string {
  const what =
    changed === 0
      ? "Tus recomendaciones se recalcularon sin productos nuevos"
      : `Tus recomendaciones cambiaron: ${changed === 1 ? "1 producto nuevo" : `${changed} productos nuevos`}`;
  const how = cache === "miss" ? "la caché se invalidó y se calcularon de nuevo en el gestor (caché: fallo)" : `caché: ${cache === null ? "sin dato" : CACHE_WORDS[cache]}`;
  return `${what}; ${how}.`;
}

/** Después de comprar: lo que ahora se le recomienda al cliente, con lo nuevo sellado. */
export function ChangedNotice({ fetching, changed, cache, result, newIds }: ChangedNoticeProps) {
  const items = result.data.recomendaciones;
  return (
    <section className="changed" aria-labelledby="changed-title">
      <p className="changed-summary" role="status">
        {fetching ? "Recalculando tus recomendaciones…" : summary(changed, cache)}
      </p>
      <Rail
        variant="fused"
        title={<span id="changed-title">Recomendado para ti, después de la compra</span>}
        emptyText="Este cliente no tiene recomendaciones."
        count={items.length}
      >
        {items.map((item) => (
          <li key={item.producto.id}>
            <ProductTile
              product={item.producto}
              reason={{ stamps: stampsOf(item), text: item.explicacion }}
              isNew={newIds.has(item.producto.id)}
            />
          </li>
        ))}
      </Rail>
    </section>
  );
}
