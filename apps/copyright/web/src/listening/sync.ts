import type { DetectedMatch } from "../api/types";

export type Side = "A" | "B";

/**
 * Lo que tolera el lado mudo antes de corregirlo: menos que una nota corta, más que el
 * desfase que dos `<audio>` acumulan entre dos cuadros.
 */
export const DRIFT_TOLERANCE_SECONDS = 0.12;

export function otherSide(side: Side): Side {
  return side === "A" ? "B" : "A";
}

/** Dónde empieza el tramo en el audio de cada lado: A es la subida, B el original. */
export function spanStart(match: DetectedMatch, side: Side): number {
  return side === "A" ? match.upload.start_seconds : match.original.start_seconds;
}

export function spanEnd(match: DetectedMatch, side: Side): number {
  return side === "A" ? match.upload.end_seconds : match.original.end_seconds;
}

/** El instante del otro lado que suena igual que `time` en `from`: el mismo punto del tramo. */
export function alignedTime(match: DetectedMatch, from: Side, time: number): number {
  return spanStart(match, otherSide(from)) + (time - spanStart(match, from));
}

/** `true` si `time` ya salió del tramo en `side`: se vuelve a su inicio (escucha en bucle). */
export function outsideSpan(match: DetectedMatch, side: Side, time: number): boolean {
  return time < spanStart(match, side) || time >= spanEnd(match, side);
}

/** Cuánto se separó el lado mudo de donde debería sonar. */
export function drift(match: DetectedMatch, leader: Side, leaderTime: number, followerTime: number): number {
  return Math.abs(followerTime - alignedTime(match, leader, leaderTime));
}
