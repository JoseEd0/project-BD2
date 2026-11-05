import type { FormEvent, ReactNode } from "react";

import { targetKey } from "../../lib/textSearch";
import type { SearchTarget } from "../../lib/textSearch";

interface SearchBarProps {
  targets: SearchTarget[];
  target: SearchTarget | null;
  query: string;
  searching: boolean;
  /** Los términos de la última respuesta; `null` mientras no hay ninguna. */
  terms: string[] | null;
  onTarget: (key: string) => void;
  onQuery: (query: string) => void;
  onSubmit: () => void;
  /** Las cláusulas de la consulta, debajo del campo. */
  children: ReactNode;
}

function SearchIcon() {
  return (
    <svg aria-hidden="true" className="searchbar__icon" viewBox="0 0 20 20">
      <circle cx="8.5" cy="8.5" r="5.5" />
      <path d="m13 13 4 4" />
    </svg>
  );
}

function AnalyzedTerms({ terms }: { terms: string[] }) {
  if (terms.length === 0) {
    return (
      <p className="analyzed">
        Solo quedaron stopwords: no hay ningún término que buscar.
      </p>
    );
  }
  return (
    <p className="analyzed">
      <span className="analyzed__label">Términos tras el análisis</span>
      {terms.map((term) => (
        <code className="analyzed__term" key={term}>
          {term}
        </code>
      ))}
    </p>
  );
}

/** Qué y dónde se busca. Lo escrito es el texto del `MATCH`; el resto de la consulta, sus
 * cláusulas, va debajo. */
export default function SearchBar({
  targets,
  target,
  query,
  searching,
  terms,
  onTarget,
  onQuery,
  onSubmit,
  children,
}: SearchBarProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <form className="searchbar" noValidate onSubmit={submit} role="search">
      <div className="searchbar__row">
        <label className="searchbar__target">
          <span className="visually-hidden">Columna en la que buscar</span>
          <select onChange={(event) => onTarget(event.target.value)} value={target === null ? "" : targetKey(target)}>
            {targets.map((item) => (
              <option key={targetKey(item)} value={targetKey(item)}>
                {targetKey(item)}
              </option>
            ))}
          </select>
        </label>
        <label className="searchbar__field">
          <SearchIcon />
          <span className="visually-hidden">Texto a buscar</span>
          <input
            autoComplete="off"
            className="searchbar__input"
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Busca un producto, o combina palabras con AND, OR y NOT"
            spellCheck={false}
            type="search"
            value={query}
          />
          <span aria-live="polite" className="searchbar__state">
            {searching ? "Buscando…" : ""}
          </span>
        </label>
      </div>
      {terms !== null && query.trim().length > 0 && <AnalyzedTerms terms={terms} />}
      {children}
    </form>
  );
}
