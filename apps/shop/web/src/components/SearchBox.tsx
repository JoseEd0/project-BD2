import { useEffect, useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";

import { PARAM } from "../catalog/filters";
import { toSearchMode, type TextSearchMode } from "../catalog/searchMode";
import { usePhoto } from "../photo/PhotoContext";
import { Icon } from "./Icon";

/** Las tres maneras de buscar: dos sobre un texto y la foto. */
type SearchMode = TextSearchMode | "foto";

const MODE_LABELS: Record<SearchMode, string> = { palabras: "Palabras", aspecto: "Cómo se ve", foto: "Foto" };
const MODES: readonly SearchMode[] = ["palabras", "aspecto", "foto"];
const PLACEHOLDERS: Record<TextSearchMode, string> = {
  palabras: "En inglés: guitar strings, drum sticks…",
  aspecto: "Describe la foto en inglés: red electric guitar…",
};
const PHOTO_MODE = "foto";

function modeFromParams(params: URLSearchParams): SearchMode {
  return params.get(PARAM.mode) === PHOTO_MODE ? PHOTO_MODE : toSearchMode(params.get(PARAM.mode));
}

/** El buscador de la barra: palabras (BM25), cómo se ve (CLIP de texto a foto) o una foto. */
export function SearchBox() {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { choose, preparing } = usePhoto();
  const onCatalog = location.pathname === "/";
  const [mode, setMode] = useState<SearchMode>(() => modeFromParams(params));
  const [query, setQuery] = useState(() => params.get(PARAM.query) ?? "");
  const groupName = useId();

  useEffect(() => {
    if (onCatalog) {
      setMode(modeFromParams(params));
      setQuery(params.get(PARAM.query) ?? "");
    }
  }, [onCatalog, params]);

  const go = (next: Record<string, string>) => {
    const kept = onCatalog ? new URLSearchParams(params) : new URLSearchParams();
    kept.delete(PARAM.query);
    for (const [name, value] of Object.entries(next)) {
      kept.set(name, value);
    }
    void navigate({ pathname: "/", search: `?${kept.toString()}` });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed !== "" && mode !== PHOTO_MODE) {
      go({ [PARAM.query]: trimmed, [PARAM.mode]: mode });
    }
  };

  const pick = async (file: File | undefined) => {
    if (file !== undefined) {
      await choose(file);
      go({ [PARAM.mode]: PHOTO_MODE });
    }
  };

  return (
    <form className="search" role="search" aria-label="Buscar en el catálogo" onSubmit={submit}>
      <fieldset className="search-modes">
        <legend className="visually-hidden">Buscar por</legend>
        {MODES.map((option) => (
          <label key={option} className="search-mode">
            <input type="radio" name={groupName} value={option} checked={mode === option} onChange={() => setMode(option)} />
            <span>{MODE_LABELS[option]}</span>
          </label>
        ))}
      </fieldset>
      {mode === PHOTO_MODE ? (
        <div className="search-photo">
          <label className="button button-on-tolex">
            <Icon name="photo" size={18} />
            {preparing ? "Preparando…" : "Subir foto"}
            <input
              className="visually-hidden"
              type="file"
              accept="image/*"
              onChange={(event) => void pick(event.target.files?.[0])}
            />
          </label>
          <label className="button button-on-tolex">
            <Icon name="camera" size={18} />
            Tomar foto
            <input
              className="visually-hidden"
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(event) => void pick(event.target.files?.[0])}
            />
          </label>
        </div>
      ) : (
        <div className="search-field">
          <label className="visually-hidden" htmlFor={`${groupName}-q`}>
            {mode === "palabras" ? "Palabras a buscar, en inglés" : "Cómo se ve lo que buscas, en inglés"}
          </label>
          <input
            id={`${groupName}-q`}
            type="search"
            name="q"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={PLACEHOLDERS[mode]}
            autoComplete="off"
            enterKeyHint="search"
          />
          <button type="submit" className="search-submit" aria-label="Buscar">
            <Icon name="search" />
          </button>
        </div>
      )}
    </form>
  );
}
