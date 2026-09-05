import type { CellValue } from "../types";

const SEPARATOR = ",";
const NEWLINE = "\n";

function escape(value: CellValue): string {
  if (value === null) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Convierte el resultado en CSV con cabecera. */
export function toCsv(columns: string[], rows: CellValue[][]): string {
  const header = columns.map(escape).join(SEPARATOR);
  const body = rows.map((row) => row.map(escape).join(SEPARATOR));
  return [header, ...body].join(NEWLINE);
}

/** Formato tabular con tabuladores, que es lo que espera una hoja de cálculo al pegar. */
export function toClipboardTable(columns: string[], rows: CellValue[][]): string {
  const cell = (value: CellValue) => (value === null ? "NULL" : String(value));
  return [columns.join("\t"), ...rows.map((row) => row.map(cell).join("\t"))].join(NEWLINE);
}

export function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
