import type { Measured, Recommendations } from "../api/types";
import { formatCount, formatMilliseconds } from "../format";
import { signalRails, stampsOf, weightPercent, type SignalRail } from "../recommend/rails";
import { SIGNAL_WORDING, type Scope } from "../recommend/signals";
import { ProductTile } from "./ProductTile";
import { Rail } from "./Rail";
import { SqlDisclosure } from "./SqlDisclosure";
import { StageTimings } from "./StageTimings";
import { Stamp } from "./Stamp";

interface RecommendationRailsProps {
  readonly result: Measured<Recommendations>;
  readonly scope: Scope;
  readonly fusedTitle: string;
  /** Los productos que no estaban antes de la última compra. */
  readonly newIds?: ReadonlySet<string>;
}

const FUSED_EMPTY: Record<Scope, string> = {
  producto: "Ninguna señal propuso productos para este. Prueba con otro producto del catálogo.",
  usuario: "Este cliente no tiene compras con que recomendarle. Compra algo y vuelve aquí.",
};

/** Una señal que corrió y trajo candidatos pero pesa 0 en la fusión no deja productos en su fila. */
const ZERO_WEIGHT_EMPTY = "Esta señal encontró candidatos, pero pesa 0\u00a0% en la fusión (los pesos se ajustaron en validación), así que no aporta productos.";

function railEmptyText(rail: SignalRail, scope: Scope): string {
  const ranWithCandidates = rail.query !== null && rail.query.candidatos > 0;
  return ranWithCandidates && weightPercent(rail.weight) === 0 ? ZERO_WEIGHT_EMPTY : SIGNAL_WORDING[rail.signal].whenEmpty[scope];
}

/** La fila fundida y una fila por señal, cada recomendación con sus sellos y su frase. */
export function RecommendationRails({ result, scope, fusedTitle, newIds = new Set() }: RecommendationRailsProps) {
  const response = result.data;
  const rails = signalRails(response);
  return (
    <div className="recommendations">
      {response.arranque_en_frio && response.recomendaciones.length > 0 && (
        <p className="cold-start" role="note">
          <strong>Arranque en frío.</strong>{" "}
          {scope === "producto"
            ? "Nadie compró este producto junto con otro al menos dos veces, así que el peso de las compras conjuntas pasó a la foto y a la descripción."
            : "Tus compras no tienen pares de compras conjuntas, así que el peso pasó a la foto y a la descripción."}
        </p>
      )}
      <Rail
        variant="fused"
        title={fusedTitle}
        meta={`Las cuatro señales fundidas por su peso: ${rails
          .map((rail) => `${SIGNAL_WORDING[rail.signal].stamp.toLocaleLowerCase("es")} ${weightPercent(rail.weight)}\u00a0%`)
          .join(", ")}.`}
        aside={<StageTimings serverTiming={result.serverTiming} cache={result.cache} />}
        emptyText={FUSED_EMPTY[scope]}
        count={response.recomendaciones.length}
      >
        {response.recomendaciones.map((item) => (
          <li key={item.producto.id}>
            <ProductTile
              product={item.producto}
              reason={{ stamps: stampsOf(item), text: item.explicacion }}
              isNew={newIds.has(item.producto.id)}
            />
          </li>
        ))}
      </Rail>
      {rails.map((rail) => (
        <Rail
          key={rail.signal}
          title={
            <>
              <Stamp signal={rail.signal} />
              <span>{SIGNAL_WORDING[rail.signal].title[scope]}</span>
            </>
          }
          meta={
            rail.query === null
              ? `${SIGNAL_WORDING[rail.signal].source} · no corrió`
              : `${SIGNAL_WORDING[rail.signal].source} · ${formatCount(rail.query.candidatos)} candidatos en ${formatMilliseconds(rail.query.milisegundos)} · peso ${weightPercent(rail.weight)}\u00a0%`
          }
          aside={rail.query === null ? undefined : <SqlDisclosure query={rail.query} />}
          emptyText={railEmptyText(rail, scope)}
          count={rail.items.length}
        >
          {rail.items.map((item) => (
            <li key={item.recommendation.producto.id}>
              <ProductTile
                product={item.recommendation.producto}
                reason={{ stamps: [rail.signal], text: item.reason }}
                isNew={newIds.has(item.recommendation.producto.id)}
              />
            </li>
          ))}
        </Rail>
      ))}
    </div>
  );
}
