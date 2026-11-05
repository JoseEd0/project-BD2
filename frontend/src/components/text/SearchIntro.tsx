import SqlBlock from "../SqlBlock";
import { targetKey } from "../../lib/textSearch";
import type { SearchTarget } from "../../lib/textSearch";

interface SearchIntroProps {
  target: SearchTarget;
  /** La consulta que se ejecutará, con un texto de muestra en el `MATCH`. */
  sql: string;
  examples: string[];
  onExample: (query: string) => void;
}

/** Lo que se ve antes de escribir: la consulta que correrá cada búsqueda y algunas para
 * probar. */
export default function SearchIntro({ target, sql, examples, onExample }: SearchIntroProps) {
  return (
    <section className="searchintro">
      <p className="searchintro__lead">
        Lo que escribas va al <code>MATCH</code> de esta consulta sobre el índice invertido de{" "}
        <code>{targetKey(target)}</code>. Se ejecuta dos veces, con <code>USING TF_IDF</code> y
        con <code>USING BM25</code>, y los dos resultados se ven lado a lado.
      </p>
      <SqlBlock sql={sql} />
      <div className="searchintro__examples">
        <span className="searchintro__label">Prueba con</span>
        {examples.map((example) => (
          <button className="snippet" key={example} onClick={() => onExample(example)} type="button">
            {example}
          </button>
        ))}
      </div>
    </section>
  );
}
