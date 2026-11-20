/**
 * Corta el texto de una respuesta en trozos de texto y grupos de citas `[n]`, con la misma
 * expresión que usa el servicio para comprobarlas (`rag/citations.py`): `[2]`, `[1][3]` o
 * `[1, 3]`. Así la interfaz enlaza exactamente las citas que el servicio contó.
 */
export type AnswerSegment =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "citation"; readonly numbers: readonly number[]; readonly raw: string };

const CITATION = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
const NUMBER_SEPARATOR = /\s*,\s*/;

export function segmentAnswer(text: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(CITATION)) {
    const index = match.index;
    if (index > cursor) {
      segments.push({ kind: "text", text: text.slice(cursor, index) });
    }
    const numbers = (match[1] ?? "").split(NUMBER_SEPARATOR).map(Number);
    segments.push({ kind: "citation", numbers, raw: match[0] });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) {
    segments.push({ kind: "text", text: text.slice(cursor) });
  }
  return segments;
}

/** Si el número `n` corresponde a uno de los `sourceCount` fragmentos enviados al modelo. */
export function isValidCitation(n: number, sourceCount: number): boolean {
  return Number.isInteger(n) && n >= 1 && n <= sourceCount;
}

/** Los números válidos que cita `text`, sin repetir: mientras llega el stream también. */
export function citedNumbers(text: string, sourceCount: number): Set<number> {
  const cited = new Set<number>();
  for (const segment of segmentAnswer(text)) {
    if (segment.kind !== "citation") {
      continue;
    }
    for (const n of segment.numbers) {
      if (isValidCitation(n, sourceCount)) {
        cited.add(n);
      }
    }
  }
  return cited;
}

