import { useState } from "react";

import { chosenTarget, createIndexSql, targetKey } from "../../lib/textSearch";
import type { SearchTarget } from "../../lib/textSearch";
import SqlBlock from "../SqlBlock";

interface IndexSetupProps {
  candidates: SearchTarget[];
  demoSql: string | null;
  running: boolean;
  onRun: (sql: string) => void;
}

/** Lo que se ve mientras ninguna columna tiene índice invertido: `MATCH` lo necesita, así
 * que se ofrece crearlo sobre una de las columnas de texto que hay, o cargar el catálogo de
 * la demostración, que ya lo trae. */
export default function IndexSetup({ candidates, demoSql, running, onRun }: IndexSetupProps) {
  const [chosen, setChosen] = useState<string | null>(null);
  const candidate = chosenTarget(candidates, chosen);
  const sql = candidate === null ? null : createIndexSql(candidate);
  return (
    <section className="setup">
      <h2 className="setup__title">Ninguna columna tiene índice invertido</h2>
      <p className="setup__lead">
        <code>MATCH</code> solo busca en columnas <code>TEXT</code> o <code>VARCHAR</code> con un
        índice <code>INVERTED</code>: es el índice el que sabe qué documentos tienen cada término.
        Se construye con SPIMI y se crea con una sentencia.
      </p>
      {candidate !== null && sql !== null && (
        <div className="setup__option">
          <label className="field">
            <span className="field__label">Columna</span>
            <select
              className="field__input"
              onChange={(event) => setChosen(event.target.value)}
              value={targetKey(candidate)}
            >
              {candidates.map((item) => (
                <option key={targetKey(item)} value={targetKey(item)}>
                  {targetKey(item)}
                </option>
              ))}
            </select>
          </label>
          <SqlBlock sql={sql} />
          <button
            className="action action--primary"
            disabled={running}
            onClick={() => onRun(sql)}
            type="button"
          >
            Crear el índice sobre {targetKey(candidate)}
          </button>
        </div>
      )}
      {demoSql !== null && (
        <div className="setup__option">
          <p className="setup__lead">
            O carga el catálogo de demostración: 2 000 productos reales de Amazon España, con su
            índice invertido ya construido. Es lo mismo que deja{" "}
            <code>demos/poblar_ecommerce.py</code>.
          </p>
          <SqlBlock sql={demoSql} />
          <button
            className="action action--primary"
            disabled={running}
            onClick={() => onRun(demoSql)}
            type="button"
          >
            Cargar el catálogo de demostración
          </button>
        </div>
      )}
    </section>
  );
}
