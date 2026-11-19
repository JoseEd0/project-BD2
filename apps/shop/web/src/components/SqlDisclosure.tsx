import type { SignalQuery } from "../api/types";
import { formatCount, formatMilliseconds } from "../format";
import { formatParameter, formatSql } from "../recommend/sql";
import { Icon } from "./Icon";

/** La consulta exacta de una señal, con sus parámetros, plegada hasta que se pide. */
export function SqlDisclosure({ query }: { readonly query: SignalQuery }) {
  return (
    <details className="sql">
      <summary>
        <Icon name="code" size={18} />
        Ver SQL
      </summary>
      <div className="sql-body">
        <pre className="sql-text">
          <code>{formatSql(query.sql)}</code>
        </pre>
        <dl className="sql-parameters">
          {query.parametros.map((value, index) => (
            <div key={index}>
              <dt>${index + 1}</dt>
              <dd>{formatParameter(value)}</dd>
            </div>
          ))}
        </dl>
        <p className="sql-meta">
          {formatCount(query.candidatos)} candidatos en {formatMilliseconds(query.milisegundos)}
        </p>
      </div>
    </details>
  );
}
