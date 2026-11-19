import type { DetectedMatch } from "../api/types";

/** Colores de resaltador de `DESIGN.md` (`--ink-a` … `--ink-f`): pasado el sexto, se repiten. */
export const HIGHLIGHTER_INKS = 6;
const FIRST_LETTER = "A".charCodeAt(0);
const ALPHABET_LETTERS = 26;

/** Un tramo coincidente puesto sobre la línea de tiempo de la subida. */
export interface SpanLayout {
  readonly index: number;
  readonly match: DetectedMatch;
  /** La letra de su canción original: la misma en el trazo, la tabla y el reproductor. */
  readonly letter: string;
  /** El color de resaltador de su canción, de 0 a `HIGHLIGHTER_INKS - 1`. */
  readonly ink: number;
  /** El carril: dos tramos que se solapan no se tapan. */
  readonly lane: number;
  /** Inicio y ancho como fracción de la duración de la subida, entre 0 y 1. */
  readonly startShare: number;
  readonly widthShare: number;
  readonly weak: boolean;
}

export interface TimelineLayout {
  readonly spans: readonly SpanLayout[];
  readonly lanes: number;
}

export function songLetter(position: number): string {
  const letter = String.fromCharCode(FIRST_LETTER + (position % ALPHABET_LETTERS));
  const round = Math.floor(position / ALPHABET_LETTERS);
  return round === 0 ? letter : `${letter}${round + 1}`;
}

function clampShare(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** La posición de cada song id por orden de aparición (las coincidencias llegan por puntaje). */
function songPositions(matches: readonly DetectedMatch[]): Map<number, number> {
  const positions = new Map<number, number>();
  for (const match of matches) {
    if (!positions.has(match.song.id)) {
      positions.set(match.song.id, positions.size);
    }
  }
  return positions;
}

/** Reparte los tramos en carriles: cada uno va al primero cuyo último tramo ya terminó. */
function assignLanes(matches: readonly DetectedMatch[]): number[] {
  const order = matches.map((_, index) => index);
  order.sort((left, right) => matches[left]!.upload.start_seconds - matches[right]!.upload.start_seconds || left - right);
  const laneEnds: number[] = [];
  const lanes = new Array<number>(matches.length).fill(0);
  for (const index of order) {
    const { start_seconds: start, end_seconds: end } = matches[index]!.upload;
    const free = laneEnds.findIndex((laneEnd) => laneEnd <= start);
    const lane = free === -1 ? laneEnds.length : free;
    laneEnds[lane] = end;
    lanes[index] = lane;
  }
  return lanes;
}

/**
 * Dónde va cada coincidencia sobre una subida de `durationSeconds`: su letra y su color por
 * canción, su carril y su tramo como fracción de la duración.
 */
export function layoutSpans(matches: readonly DetectedMatch[], durationSeconds: number): TimelineLayout {
  const positions = songPositions(matches);
  const lanes = assignLanes(matches);
  const spans = matches.map((match, index): SpanLayout => {
    const position = positions.get(match.song.id) ?? 0;
    const start = clampShare(match.upload.start_seconds / durationSeconds);
    const end = clampShare(match.upload.end_seconds / durationSeconds);
    return {
      index,
      match,
      letter: songLetter(position),
      ink: position % HIGHLIGHTER_INKS,
      lane: lanes[index] ?? 0,
      startShare: start,
      widthShare: Math.max(0, end - start),
      weak: !match.above_threshold,
    };
  });
  return { spans, lanes: spans.length === 0 ? 0 : Math.max(...lanes) + 1 };
}
