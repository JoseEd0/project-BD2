import { useRef } from "react";

import { formatBytes, formatClock } from "../analysis/format";
import { Icon } from "../components/Icon";
import { ACCEPTED_AUDIO } from "./DropZone";

interface FileBarProps {
  readonly file: File;
  readonly durationSeconds: number | null;
  readonly busy: boolean;
  readonly onFile: (file: File) => void;
  readonly onCancel: () => void;
}

/** El archivo que se está analizando y cómo cambiarlo o cancelarlo. */
export function FileBar({ file, durationSeconds, busy, onFile, onCancel }: FileBarProps) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="filebar">
      <Icon name="wave" />
      <div className="filebar__name">
        <span className="filebar__title" title={file.name}>
          {file.name}
        </span>
        <span className="filebar__facts measure">
          {formatBytes(file.size)}
          {durationSeconds !== null && ` · ${formatClock(durationSeconds)}`}
        </span>
      </div>
      {busy ? (
        <button type="button" className="button button--quiet" onClick={onCancel}>
          <Icon name="close" />
          Cancelar
        </button>
      ) : (
        <button type="button" className="button button--quiet" onClick={() => input.current?.click()}>
          <Icon name="upload" />
          Analizar otro
        </button>
      )}
      <input
        ref={input}
        className="visually-hidden"
        type="file"
        accept={ACCEPTED_AUDIO}
        tabIndex={-1}
        aria-label="Otro audio para analizar"
        onChange={(event) => {
          const chosen = event.target.files?.item(0);
          if (chosen) {
            onFile(chosen);
          }
          event.target.value = "";
        }}
      />
    </div>
  );
}
