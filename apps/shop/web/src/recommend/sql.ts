/** Las cláusulas que empiezan línea al mostrar una consulta, de la más larga a la más corta. */
const CLAUSES = ["GROUP BY", "ORDER BY", "HAVING", "WHERE", "FROM", "JOIN", "LIMIT"];
const CLAUSE_BREAK = new RegExp(`\\s+(?=(?:${CLAUSES.join("|")})\\b)`, "g");

/** El SQL tal como corrió, con una cláusula por línea para leerlo. */
export function formatSql(sql: string): string {
  return sql.replace(CLAUSE_BREAK, "\n");
}

function isPoint(value: unknown): value is { lat: number; lon: number } {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).lat === "number" &&
    typeof (value as Record<string, unknown>).lon === "number"
  );
}

/** Un parámetro como se escribiría en el SQL: texto entre comillas, un punto como `POINT`. */
export function formatParameter(value: unknown): string {
  if (typeof value === "string") {
    return /^vector\(\d+\)$/.test(value) ? value : `'${value.replace(/'/g, "''")}'`;
  }
  if (isPoint(value)) {
    return `POINT(${value.lat}, ${value.lon})`;
  }
  if (value === null || value === undefined) {
    return "NULL";
  }
  return typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value);
}
