import type { Detection } from "../api/types";
import { formatPercent } from "./format";

export type VerdictTone = "copy" | "weak" | "clear" | "silent";

export interface Verdict {
  readonly tone: VerdictTone;
  readonly headline: string;
  readonly detail: string;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/** La conclusión de una detección en lenguaje claro: qué decir y con qué tono. */
export function verdictOf(detection: Detection): Verdict {
  const copies = detection.matches.filter((match) => match.above_threshold);
  if (copies.length > 0) {
    const songs = new Set(copies.map((match) => match.song.id)).size;
    return {
      tone: "copy",
      headline: `Posible uso no autorizado: ${plural(copies.length, "fragmento", "fragmentos")} de ${plural(songs, "canción", "canciones")}`,
      detail: "Escucha cada tramo contra su original antes de decidir.",
    };
  }
  if (detection.matches.length > 0) {
    return {
      tone: "weak",
      headline: "No encontramos copias claras",
      detail: `${plural(detection.matches.length, "coincidencia queda", "coincidencias quedan")} bajo el umbral de ${formatPercent(detection.threshold)}: escúchalas para descartarlas.`,
    };
  }
  if (detection.windows > 0 && detection.silent_windows === detection.windows) {
    return {
      tone: "silent",
      headline: "El audio está en silencio",
      detail: `Ninguna de sus ${plural(detection.windows, "ventana", "ventanas")} tiene sonido que comparar.`,
    };
  }
  return {
    tone: "clear",
    headline: "No encontramos coincidencias con el catálogo",
    detail: `Ningún tramo de sus ${plural(detection.windows, "ventana", "ventanas")} se alinea con una canción del catálogo.`,
  };
}
