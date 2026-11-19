/**
 * Dónde está un pasaje del corpus dentro del texto de una página del PDF.
 *
 * El corpus limpió el texto (unió los guiones de fin de línea, quitó encabezados y números de
 * página) y pdf.js entrega la página en trozos con otros espacios y ligaduras. Se comparan las
 * dos versiones reducidas a letras y dígitos en minúscula, y se busca el principio y el final
 * del pasaje con anclas cortas. Un fragmento que cruza de página (`page_start` < `page_end`) se
 * marca desde arriba en la página donde termina y hasta abajo en la que empieza: el resto de su
 * texto está en la otra página, y buscarlo en esta solo puede dar un falso positivo.
 */
export interface PassageBounds {
  /** El fragmento empezó en una página anterior. */
  readonly startsBefore: boolean;
  /** El fragmento sigue en la página siguiente. */
  readonly endsAfter: boolean;
}

export const WITHIN_PAGE: PassageBounds = { startsBefore: false, endsAfter: false };

export interface PassageSpan {
  /** Índices de los trozos de texto de la página, ambos incluidos. */
  readonly first: number;
  readonly last: number;
}

/** Caracteres de las anclas: bastan para no confundir el lugar y sobreviven a la limpieza. */
const ANCHOR_LENGTH = 48;
/** Posiciones del pasaje desde las que se prueba un ancla, si la primera no aparece. */
const ANCHOR_ATTEMPTS = 6;
const ANCHOR_STEP = 24;

export function normalizeForMatch(text: string): string {
  return text
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function itemOffsets(items: readonly string[]): { text: string; ends: number[] } {
  let text = "";
  const ends: number[] = [];
  for (const item of items) {
    text += normalizeForMatch(item);
    ends.push(text.length);
  }
  return { text, ends };
}

function itemAt(ends: readonly number[], offset: number): number {
  const index = ends.findIndex((end) => end > offset);
  return index < 0 ? ends.length - 1 : index;
}

function findStart(page: string, passage: string): number | null {
  for (let attempt = 0; attempt < ANCHOR_ATTEMPTS; attempt += 1) {
    const from = attempt * ANCHOR_STEP;
    const anchor = passage.slice(from, from + ANCHOR_LENGTH);
    if (anchor.length < ANCHOR_LENGTH / 2) {
      return null;
    }
    // Se marca desde donde aparece el ancla: si las primeras palabras no están en la página
    // (un pie de figura que la limpieza movió), no se inventa dónde estarían.
    const found = page.indexOf(anchor);
    if (found >= 0) {
      return found;
    }
  }
  return null;
}

function findEnd(page: string, passage: string, from: number): number | null {
  for (let attempt = 0; attempt < ANCHOR_ATTEMPTS; attempt += 1) {
    const to = passage.length - attempt * ANCHOR_STEP;
    const anchor = passage.slice(Math.max(0, to - ANCHOR_LENGTH), to);
    if (anchor.length < ANCHOR_LENGTH / 2) {
      return null;
    }
    const found = page.indexOf(anchor, from);
    if (found >= 0) {
      return found + anchor.length - 1;
    }
  }
  return null;
}

/**
 * Los trozos de `items` (el texto de una página, en el orden de pdf.js) que cubre `passage`,
 * o `null` si no aparece ni su principio ni su final.
 */
export function locatePassage(items: readonly string[], passage: string, bounds: PassageBounds = WITHIN_PAGE): PassageSpan | null {
  const page = itemOffsets(items);
  const target = normalizeForMatch(passage);
  if (page.text === "" || target === "") {
    return null;
  }
  const lastItem = items.length - 1;
  const foundStart = bounds.startsBefore ? 0 : findStart(page.text, target);
  const foundEnd = bounds.endsAfter ? page.text.length - 1 : findEnd(page.text, target, foundStart ?? 0);
  if (foundStart === null && foundEnd === null) {
    return null;
  }
  // Con una sola punta, la otra se estima por el largo del pasaje.
  const start = foundStart ?? Math.max(0, (foundEnd ?? 0) - target.length + 1);
  const end = foundEnd ?? Math.min(page.text.length - 1, start + target.length - 1);
  const first = bounds.startsBefore ? 0 : itemAt(page.ends, start);
  const last = bounds.endsAfter ? lastItem : itemAt(page.ends, end);
  return last < first ? null : { first, last };
}
