import { formatCost, formatCount, formatDuration } from "../format";
import type { TokenUsage } from "../research/answerEvents";
import type { Turn } from "../research/turns";
import { StageBars } from "./StageBars";

const STAGE_LABELS: Readonly<Record<string, string>> = {
  filter: "Papers del filtro",
  embedding: "Embedding de la pregunta",
  bm25: "BM25",
  vector: "HNSW",
  papers: "Metadatos de papers",
};

const TOKEN_ROWS: readonly { key: keyof TokenUsage; label: string }[] = [
  { key: "input_tokens", label: "Entrada" },
  { key: "cache_read_input_tokens", label: "Entrada leída de caché" },
  { key: "cache_creation_input_tokens", label: "Entrada escrita en caché" },
  { key: "output_tokens", label: "Salida (con razonamiento)" },
];

const STOP_REASONS: Readonly<Record<string, string>> = {
  end_turn: "terminó",
  max_tokens: "cortada por el tope de longitud",
  refusal: "rechazada",
};

/** Dónde se fue el tiempo de una respuesta, cuántos tokens usó y cuánto costó. */
export function MeasuresPane({ turn }: { readonly turn: Turn }) {
  const done = turn.done;
  return (
    <section className="measures" aria-label="Tiempos y costo">
      <StageBars serverTiming={turn.serverTiming} labels={STAGE_LABELS} caption="Recuperación en el servidor, antes del modelo" />
      <dl className="facts">
        <div>
          <dt>Hasta el primer texto</dt>
          <dd>{turn.firstTextMs === null ? "—" : formatDuration(turn.firstTextMs)}</dd>
        </div>
        <div>
          <dt>Respuesta completa</dt>
          <dd>{turn.totalMs === null ? "en curso" : formatDuration(turn.totalMs)}</dd>
        </div>
        <div>
          <dt>Modelo</dt>
          <dd>{turn.model ?? "—"}</dd>
        </div>
        {done !== null && (
          <div>
            <dt>Cierre</dt>
            <dd>{STOP_REASONS[done.stop_reason] ?? done.stop_reason}</dd>
          </div>
        )}
      </dl>
      <p className="measures__note">Los tiempos de arriba los mide el navegador desde que se envió la pregunta.</p>
      {done === null ? (
        <p className="muted">Los tokens y el costo llegan al terminar la respuesta.</p>
      ) : (
        <>
          <table className="tokens">
            <caption>Tokens</caption>
            <tbody>
              {TOKEN_ROWS.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{row.label}</th>
                  <td className="num">{formatCount(done.usage[row.key])}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Costo</th>
                <td className="num">
                  <strong>{formatCost(done.cost_usd)}</strong>
                </td>
              </tr>
            </tfoot>
          </table>
          {done.attempts.length > 1 && (
            <table className="tokens">
              <caption>Intentos</caption>
              <tbody>
                {done.attempts.map((attempt, index) => (
                  <tr key={index}>
                    <th scope="row">{attempt.model}</th>
                    <td className="num">
                      {formatCount(attempt.usage.input_tokens)} → {formatCount(attempt.usage.output_tokens)}
                    </td>
                    <td className="num">{formatCost(attempt.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
