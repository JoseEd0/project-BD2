import { useRef, useState } from "react";
import type { DragEvent } from "react";

import FailureNotice from "../FailureNotice";
import { mediaKindOf } from "../../lib/mediaSearch";
import type { MediaCard, MediaQuery } from "../../lib/mediaSearch";
import type { QueryFailure } from "../../types";
import MediaThumb from "./MediaThumb";

const ACCEPTED_FILES = "image/*,audio/*,.mp3,.wav,.flac,.ogg";

interface QueryPickerProps {
  query: MediaQuery | null;
  samples: MediaCard[];
  uploading: boolean;
  failure: QueryFailure | null;
  onUpload: (file: File) => void;
  onPick: (card: MediaCard) => void;
}

function CurrentQuery({ query }: { query: MediaQuery }) {
  return (
    <figure className="mediaquery__current">
      <MediaThumb kind={query.kind} label={query.name} path={query.path} />
      <figcaption className="mediaquery__caption">
        <span className="mediaquery__name" title={query.name}>
          {query.name}
        </span>
        {query.category !== null && <span className="badge">{query.category}</span>}
        <code className="mediaquery__path" title={query.path}>
          {query.path}
        </code>
      </figcaption>
    </figure>
  );
}

/**
 * De dónde sale el archivo de la consulta: uno que se arrastra o se elige, y que el API
 * guarda para `SIMILAR_TO`, o el de una fila de la tabla, cuya clase sirve para ver cuántos
 * vecinos la comparten.
 */
export default function QueryPicker({
  query,
  samples,
  uploading,
  failure,
  onUpload,
  onPick,
}: QueryPickerProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const drop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file !== undefined) {
      onUpload(file);
    }
  };

  return (
    <section aria-label="Archivo de la consulta" className="mediaquery">
      <div
        className={dragging ? "mediadrop mediadrop--active" : "mediadrop"}
        onDragLeave={() => setDragging(false)}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDrop={drop}
      >
        {query === null ? (
          <p className="mediadrop__lead">Arrastra una imagen o un audio aquí</p>
        ) : (
          <CurrentQuery query={query} />
        )}
        <button
          className="action"
          disabled={uploading}
          onClick={() => input.current?.click()}
          type="button"
        >
          {uploading ? "Subiendo…" : query === null ? "Elegir archivo" : "Cambiar archivo"}
        </button>
        <input
          accept={ACCEPTED_FILES}
          className="visually-hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) {
              onUpload(file);
            }
            event.target.value = "";
          }}
          ref={input}
          tabIndex={-1}
          type="file"
        />
        {failure !== null && <FailureNotice failure={failure} />}
      </div>

      <div className="samples">
        <h2 className="samples__title">O una fila de la tabla</h2>
        {samples.length === 0 ? (
          <p className="samples__empty">Leyendo las primeras filas…</p>
        ) : (
          <ul className="samples__list">
            {samples.map((card) => {
              const kind = mediaKindOf(card.path);
              const chosen = query?.path === card.path;
              return (
                <li key={card.key}>
                  <button
                    aria-pressed={chosen}
                    className={chosen ? "sample sample--chosen" : "sample"}
                    disabled={kind === null}
                    onClick={() => onPick(card)}
                    title={card.title ?? card.path}
                    type="button"
                  >
                    {kind !== null && (
                      <MediaThumb kind={kind} label={card.title ?? card.path} path={card.path} playable={false} />
                    )}
                    <span className="sample__label">{card.title ?? card.key}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
