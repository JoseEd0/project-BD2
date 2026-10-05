import type { CellValue, GeoPoint, Overlay, RTreeLevel } from "../types";
import { formatPoint, isGeoPoint } from "./points";

/** Vértices con los que se aproxima un círculo trazado en grados. */
const RING_SEGMENTS = 72;
const FULL_TURN = 2 * Math.PI;
const METRES_PER_KILOMETRE = 1000;
const PERCENT = 100;

/** Una fila del resultado que trae un punto y, por tanto, se puede situar en el mapa. */
export interface LocatedRow {
  point: GeoPoint;
  row: CellValue[];
}

/** Filas con ubicación: de cada una se toma su primera celda de tipo punto. */
export function locatedRows(rows: CellValue[][]): LocatedRow[] {
  const located: LocatedRow[] = [];
  for (const row of rows) {
    const point = row.find(isGeoPoint);
    if (point !== undefined) {
      located.push({ point, row });
    }
  }
  return located;
}

/**
 * Contorno de un radio euclidiano. La distancia euclidiana se mide en grados, así que su
 * «círculo» lo es en el plano de coordenadas y no sobre el terreno: en el mapa se ve
 * achatado, tanto más cuanto más lejos del ecuador.
 */
export function euclideanRing(center: GeoPoint, radius: number): [number, number][] {
  return Array.from({ length: RING_SEGMENTS }, (_, step) => {
    const angle = (FULL_TURN * step) / RING_SEGMENTS;
    return [center.lat + radius * Math.sin(angle), center.lon + radius * Math.cos(angle)];
  });
}

function radiusText(overlay: Overlay): string {
  const radius = overlay.radius ?? 0;
  if (overlay.unit === "m" && radius >= METRES_PER_KILOMETRE) {
    return `${(radius / METRES_PER_KILOMETRE).toLocaleString("es")} km`;
  }
  return `${radius.toLocaleString("es")} ${overlay.unit ?? ""}`.trim();
}

/** Descripción de una figura para la leyenda del mapa. */
export function overlayLabel(overlay: Overlay): string {
  if (overlay.kind === "polygon") {
    return `Polígono de ${overlay.vertices.length} vértices`;
  }
  const center = overlay.center === null ? "" : formatPoint(overlay.center);
  if (overlay.kind === "nearest") {
    return `Más cercanas a ${center}, por ${overlay.metric}`;
  }
  return `Radio de ${radiusText(overlay)} alrededor de ${center}, por ${overlay.metric}`;
}

/** Ocupación media de los nodos de un nivel, en porcentaje de su capacidad. */
export function averageFill(level: RTreeLevel, capacity: number): number {
  if (level.node_count === 0) return 0;
  return (level.entry_count / (level.node_count * capacity)) * PERCENT;
}
