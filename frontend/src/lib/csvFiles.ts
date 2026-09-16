/** Lo que el diálogo de carga necesita saber de un CSV antes de subirlo. */

const HEADER_SAMPLE_BYTES = 64 * 1024;
const QUOTE = '"';

/** Nombres de la cabecera, leyendo solo el principio del archivo. */
export async function readCsvHeader(file: File): Promise<string[]> {
  const sample = await file.slice(0, HEADER_SAMPLE_BYTES).text();
  const firstLine = sample.split(/\r?\n/, 1)[0] ?? "";
  return splitCsvLine(firstLine)
    .map((name) => name.trim())
    .filter((name) => name.length > 0);
}

function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let quoted = false;
  for (const character of line) {
    if (character === QUOTE) {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      cells.push(current);
      current = "";
    } else {
      current += character;
    }
  }
  cells.push(current);
  return cells;
}

/** Nombre de tabla válido propuesto a partir del nombre del archivo. */
export function tableNameFrom(file: File): string {
  return file.name
    .replace(/\.[^.]+$/, "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9_]/g, "_")
    .replace(/^([^A-Za-z_])/, "t$1");
}

/**
 * Plantilla de `CREATE TABLE ... FROM FILE` para un archivo ya subido. Deja escrita la
 * opción más habitual y, en comentarios, cómo cambiarla por cada organización.
 */
export function createFromFileSql(name: string, path: string, columns: string[]): string {
  const key = columns.includes("id") ? "id" : (columns[0] ?? "id");
  return `-- Archivo subido: ${path}
-- Columnas: ${columns.join(", ")}
--
-- Elige cómo guardar la tabla cambiando la línea USING INDEX:
--   USING INDEX BTREE("${key}")   B+ agrupado: las filas viven en el árbol
--   USING INDEX SEQ("${key}")     archivo secuencial ordenado por la clave
--   USING INDEX HASH("${key}")    heap file + índice hash sobre la clave
--   (sin USING INDEX)            heap file sin clave
CREATE TABLE ${name} FROM FILE '${path}'
  USING INDEX BTREE("${key}");`;
}
