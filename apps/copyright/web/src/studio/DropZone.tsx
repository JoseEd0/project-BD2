import { useId, useRef, useState, type DragEvent, type ReactNode } from "react";

import { Icon } from "../components/Icon";

export const ACCEPTED_AUDIO = "audio/*,.mp3,.wav,.flac,.ogg,.oga";

interface DropZoneProps {
  readonly onFile: (file: File) => void;
  readonly disabled: boolean;
  readonly notice: string | null;
  readonly children?: ReactNode;
}

function firstFile(event: DragEvent): File | null {
  return event.dataTransfer.files.item(0);
}

/** Un pentagrama en blanco que invita a subir: se arrastra encima o se elige con el botón. */
export function DropZone({ onFile, disabled, notice, children }: DropZoneProps) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const hintId = useId();
  const accept = (file: File | null) => {
    if (file !== null && !disabled) {
      onFile(file);
    }
  };
  return (
    <section
      className="dropzone"
      data-over={over}
      data-disabled={disabled}
      aria-labelledby="dropzone-title"
      onDragOver={(event) => {
        event.preventDefault();
        setOver(!disabled);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        accept(firstFile(event));
      }}
    >
      <div className="dropzone__staff" aria-hidden="true" />
      <div className="dropzone__body">
        <h1 id="dropzone-title" className="dropzone__title">
          ¿Esta canción usa música del catálogo?
        </h1>
        <p id={hintId} className="dropzone__hint">
          Arrastra aquí un audio o elígelo. Te mostramos qué tramos coinciden con qué canción original y los escuchas uno contra otro.
        </p>
        <div className="dropzone__actions">
          <button type="button" className="button button--primary" disabled={disabled} aria-describedby={hintId} onClick={() => input.current?.click()}>
            <Icon name="upload" />
            Elegir un audio
          </button>
          <span className="dropzone__formats">MP3, WAV, FLAC u OGG · hasta 48 MB y 10 min</span>
        </div>
        {notice !== null && (
          <p className="dropzone__notice" role="status">
            {notice}
          </p>
        )}
        {children}
      </div>
      <input
        ref={input}
        className="visually-hidden"
        type="file"
        accept={ACCEPTED_AUDIO}
        tabIndex={-1}
        aria-label="Audio para analizar"
        onChange={(event) => {
          accept(event.target.files?.item(0) ?? null);
          event.target.value = "";
        }}
      />
    </section>
  );
}
