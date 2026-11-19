import { lazy, Suspense, useState } from "react";

import { useStoresWithStock } from "../api/shop";
import { ProblemNotice } from "../components/ProblemNotice";
import { NEARBY_RADIUS_METERS } from "../config";
import { formatCount, formatDistance } from "../format";
import { LocationControls } from "../location/LocationControls";
import { useShopperLocation } from "../location/LocationContext";

const StockMap = lazy(() => import("../location/StockMap"));

/** Dónde hay stock de este producto: el mapa y su lista equivalente, de la tienda más cercana a la más lejana. */
export function StockPanel({ productId }: { readonly productId: string }) {
  const { point, choosePoint } = useShopperLocation();
  const stores = useStoresWithStock(productId, point);
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const list = stores.data ?? [];
  const nearby = list.filter((store) => store.metros <= NEARBY_RADIUS_METERS).length;

  return (
    <section className="stock" aria-labelledby="stock-title">
      <header className="stock-head">
        <h2 id="stock-title">Dónde hay stock</h2>
        <LocationControls choosing={choosing} onToggleChoosing={() => setChoosing((value) => !value)} />
      </header>
      <div className="stock-body">
        <div className="stock-map">
          <Suspense fallback={<div className="map map-loading" />}>
            <StockMap
              point={point}
              stores={list}
              selectedStore={selected}
              choosing={choosing}
              onChoose={(next) => {
                choosePoint(next);
                setChoosing(false);
              }}
              onSelectStore={setSelected}
            />
          </Suspense>
        </div>
        <div className="stock-list">
          {stores.isError ? (
            <ProblemNotice title={stores.error.title} detail={stores.error.detail} onRetry={() => void stores.refetch()} />
          ) : stores.isPending ? (
            <p className="muted" role="status">
              Buscando tiendas…
            </p>
          ) : list.length === 0 ? (
            <p className="empty-note">Ninguna tienda de Lima tiene este producto en stock.</p>
          ) : (
            <>
              <p className="stock-summary">
                {nearby === 0
                  ? `Ninguna a menos de ${formatDistance(NEARBY_RADIUS_METERS)}; estas son las más cercanas.`
                  : `${nearby === 1 ? "1 tienda" : `${nearby} tiendas`} a menos de ${formatDistance(NEARBY_RADIUS_METERS)}.`}
              </p>
              <ol className="stores">
                {list.map((store, index) => (
                  <li key={store.id}>
                    <button
                      type="button"
                      className="store"
                      aria-pressed={store.id === selected}
                      onClick={() => setSelected(store.id)}
                    >
                      <span className="store-number" aria-hidden="true">
                        {index + 1}
                      </span>
                      <span className="store-name">{store.nombre}</span>
                      <span className="store-kind">{store.tipo}</span>
                      <span className="store-facts">
                        <span>{formatDistance(store.metros)}</span>
                        <span>
                          {store.unidades === 1 ? "1 unidad" : `${formatCount(store.unidades)} unidades`}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
