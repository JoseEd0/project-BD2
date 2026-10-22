import type { ColumnInfo } from "../types";

export const ORGANIZATION_LABEL: Record<string, string> = {
  heap: "heap file",
  sequential: "secuencial",
  clustered_btree: "B+ agrupado",
};

export function typeLabel(column: ColumnInfo): string {
  return column.length === null ? column.type : `${column.type}(${column.length})`;
}

/** Los métodos de los índices de la columna, juntos; `null` si no tiene ninguno. */
export function indexLabel(column: ColumnInfo): string | null {
  return column.indexed_with.length === 0 ? null : column.indexed_with.join(" · ");
}

export function isIndexedWith(column: ColumnInfo | undefined, method: string): boolean {
  return column?.indexed_with.includes(method) ?? false;
}
