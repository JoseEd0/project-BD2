import type { CellValue, GeoPoint } from "../types";

/** Decimales con los que se muestra una coordenada: unos 10 cm sobre el terreno. */
const COORDINATE_DECIMALS = 6;

export function isGeoPoint(value: unknown): value is GeoPoint {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<GeoPoint>;
  return typeof candidate.lat === "number" && typeof candidate.lon === "number";
}

function coordinate(value: number): string {
  return Number(value.toFixed(COORDINATE_DECIMALS)).toString();
}

/** `(-12.0464, -77.0428)`: latitud y longitud, como se leen en la tabla de resultados. */
export function formatPoint(point: GeoPoint): string {
  return `(${coordinate(point.lat)}, ${coordinate(point.lon)})`;
}

/** `POINT(-12.0464, -77.0428)`: la forma que aceptan el SQL del gestor y su carga de CSV. */
export function pointLiteral(point: GeoPoint): string {
  return `POINT(${coordinate(point.lat)}, ${coordinate(point.lon)})`;
}

/** Texto de una celda para copiar o exportar; `emptyText` es lo que se escribe por un NULL. */
export function cellText(value: CellValue, emptyText: string): string {
  if (value === null) return emptyText;
  return isGeoPoint(value) ? pointLiteral(value) : String(value);
}
