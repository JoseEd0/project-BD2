import { formatPercent } from "../analysis/format";
import type { Phase } from "./useStudio";

/** Subiendo, con su porcentaje; analizando, sin uno, porque el servicio no informa avance. */
export function Progress({ phase }: { readonly phase: Phase }) {
  if (phase.kind === "fetching") {
    return (
      <div className="progress" role="status">
        <span className="progress__label">Preparando «{phase.title}»…</span>
        <progress className="progress__bar" aria-label="Preparación del audio" />
      </div>
    );
  }
  if (phase.kind === "uploading") {
    const known = phase.progress !== null;
    return (
      <div className="progress" role="status">
        <span className="progress__label">
          Subiendo {known && <span className="measure">{formatPercent(phase.progress ?? 0)}</span>}
        </span>
        <progress className="progress__bar" max={1} value={phase.progress ?? undefined} aria-label="Subida del audio" />
      </div>
    );
  }
  if (phase.kind === "analyzing") {
    return (
      <div className="progress" role="status">
        <span className="progress__label">Analizando: buscando cada ventana de 5 s en el catálogo…</span>
        <progress className="progress__bar" aria-label="Análisis en curso" />
      </div>
    );
  }
  return null;
}
