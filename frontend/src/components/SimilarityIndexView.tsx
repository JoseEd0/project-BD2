import type { SimilarityStructure } from "../types";
import Stat from "./Stat";

const PERCENT = 100;
const MEDIA_LABEL = { image: "imágenes (SIFT)", audio: "audios (MFCC)" } as const;

/** Una fila por nivel del grafo: cuántos nodos llegan a él, con una barra proporcional. */
function LevelBars({ levels }: { levels: number[] }) {
  const base = levels[0] ?? 0;
  return (
    <div className="tablescroll">
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Nivel</th>
            <th>Nodos</th>
            <th>Del total</th>
          </tr>
        </thead>
        <tbody>
          {[...levels.keys()].reverse().map((level) => (
            <tr key={level}>
              <td className="is-number">{level}</td>
              <td className="is-number">{levels[level].toLocaleString("es")}</td>
              <td className="levelbar">
                <span className="levelbar__fill" style={{ width: `${(levels[level] / Math.max(base, 1)) * PERCENT}%` }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** El tamaño de cada lista de IVF, de la más llena a la más vacía. */
function ListBars({ sizes }: { sizes: number[] }) {
  const sorted = [...sizes].sort((a, b) => b - a);
  const largest = sorted[0] ?? 0;
  return (
    <div aria-label="Vectores por lista, de la más llena a la más vacía" className="listbars" role="img">
      {sorted.map((size, index) => (
        <span
          className="listbars__bar"
          key={index}
          style={{ height: `${(size / Math.max(largest, 1)) * PERCENT}%` }}
          title={`${size} vectores`}
        />
      ))}
    </div>
  );
}

/** La forma física de un índice HNSW o IVF y de su almacén de características. */
export default function SimilarityIndexView({ index }: { index: SimilarityStructure }) {
  const features = index.features;
  return (
    <div className="indexview">
      <div className="stats">
        <Stat label="vectores" value={index.vectors ?? 0} />
        <Stat label="dimensión (palabras)" value={index.dimension ?? 0} />
        <Stat label="métrica" value={index.metric} />
        {index.media !== null && <Stat label="archivos" value={MEDIA_LABEL[index.media]} />}
        {features.words_in_use !== undefined && <Stat label="palabras en uso" value={features.words_in_use} />}
        {features.pages !== undefined && <Stat label="páginas del almacén" value={features.pages} />}
      </div>
      {index.kind === "hnsw" ? (
        <>
          <div className="stats">
            <Stat label="M" value={index.m ?? 0} />
            <Stat label="ef_construction" value={index.ef_construction ?? 0} />
            <Stat label="ef_search" value={index.ef_search ?? 0} />
            <Stat label="lápidas" value={index.tombstones ?? 0} />
            <Stat label="vecinos por nodo (nivel 0)" value={(index.mean_base_degree ?? 0).toFixed(1)} />
          </div>
          <LevelBars levels={index.nodes_per_level ?? []} />
        </>
      ) : (
        <>
          <div className="stats">
            <Stat label="listas" value={index.lists ?? 0} />
            <Stat label="probes por omisión" value={index.probes ?? 0} />
            <Stat label="entrenado con" value={index.trained_on ?? 0} />
            <Stat label="desbalance" value={(index.imbalance ?? 0).toFixed(2)} />
          </div>
          <ListBars sizes={index.list_sizes ?? []} />
        </>
      )}
    </div>
  );
}
