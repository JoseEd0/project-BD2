import type { CellValue } from "../types";
import { formatPoint, isGeoPoint, pointLiteral } from "./points";

/** Dígitos significativos que bastan para quitar el ruido binario de un doble. */
const SIGNIFICANT_DIGITS = 12;
export const NULL_TEXT = "NULL";

function formatNumber(value: number): string {
  if (Number.isInteger(value)) {
    return value.toString();
  }
  return Number(value.toPrecision(SIGNIFICANT_DIGITS)).toString();
}

/** Una celda tal como se lee en pantalla: en la tabla de resultados y en la ficha del mapa. */
export function displayCell(value: CellValue): string {
  if (value === null) return NULL_TEXT;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return formatNumber(value);
  if (isGeoPoint(value)) return formatPoint(value);
  return String(value);
}

/** Una celda para copiar o exportar: un punto, en la forma que el gestor vuelve a cargar.
 * `emptyText` es lo que se escribe por un NULL. */
export function cellText(value: CellValue, emptyText: string): string {
  if (value === null) return emptyText;
  return isGeoPoint(value) ? pointLiteral(value) : String(value);
}
