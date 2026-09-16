/** Resaltado de SQL sin dependencias: se tokeniza y cada trozo recibe su clase. */

export type TokenKind =
  | "keyword"
  | "type"
  | "function"
  | "string"
  | "number"
  | "comment"
  | "operator"
  | "punctuation"
  | "plain";

export interface Token {
  text: string;
  kind: TokenKind;
}

const KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
  "CREATE", "DROP", "TABLE", "INDEX", "USING", "WITH", "ORDER", "GROUP", "BY", "HAVING",
  "LIMIT", "OFFSET", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "ON", "AS",
  "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "BETWEEN", "DISTINCT", "ASC", "DESC",
  "PRIMARY", "KEY", "UNIQUE", "IF", "EXISTS", "FILE", "BEGIN", "END", "COMMIT",
  "ROLLBACK", "TRANSACTION", "TRUE", "FALSE", "EXPLAIN", "ANALYZE",
]);

const TYPES = new Set([
  "INT", "INTEGER", "BIGINT", "FLOAT", "DOUBLE", "REAL", "BOOL", "BOOLEAN", "CHAR",
  "VARCHAR", "TEXT", "DATE", "POINT", "VECTOR", "ARRAY", "BLOB",
  "BTREE", "HASH", "SEQ", "SEQUENTIAL", "RTREE", "IVF", "HNSW", "SPIMI", "INVERTED",
  "TF_IDF", "BM25",
]);

const FUNCTIONS = new Set([
  "COUNT", "SUM", "AVG", "MIN", "MAX", "MATCH", "SCORE", "SIMILAR_TO",
  "SIMILARITY_SCORE", "DISTANCIA",
]);

const PATTERNS: { kind: TokenKind; regex: RegExp }[] = [
  { kind: "comment", regex: /^--[^\n]*|^\/\*[\s\S]*?(?:\*\/|$)/ },
  { kind: "string", regex: /^'(?:''|[^'])*'?|^"(?:""|[^"])*"?/ },
  { kind: "number", regex: /^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/ },
  { kind: "plain", regex: /^[A-Za-z_$][A-Za-z0-9_$]*/ },
  { kind: "operator", regex: /^(?:<>|!=|<=|>=|==|[=<>+\-*/%])/ },
  { kind: "punctuation", regex: /^[(),.;]/ },
  { kind: "plain", regex: /^\s+/ },
];

function classify(word: string): TokenKind {
  const upper = word.toUpperCase();
  if (KEYWORDS.has(upper)) return "keyword";
  if (TYPES.has(upper)) return "type";
  if (FUNCTIONS.has(upper)) return "function";
  return "plain";
}

/** Divide el SQL en trozos etiquetados, conservando espacios y saltos de línea. */
export function tokenize(sql: string): Token[] {
  const tokens: Token[] = [];
  let rest = sql;
  while (rest.length > 0) {
    const matched = PATTERNS.find((pattern) => pattern.regex.test(rest));
    if (!matched) {
      tokens.push({ text: rest[0], kind: "plain" });
      rest = rest.slice(1);
      continue;
    }
    const [text] = matched.regex.exec(rest) as RegExpExecArray;
    const kind = matched.kind === "plain" && /^[A-Za-z_$]/.test(text) ? classify(text) : matched.kind;
    tokens.push({ text, kind });
    rest = rest.slice(text.length);
  }
  return tokens;
}

const IGNORED_KINDS = new Set<TokenKind>(["comment"]);

/**
 * Cuenta las sentencias con el mismo tokenizador del resaltado, para que un `;` dentro de
 * una cadena o de un comentario no cuente como separador.
 */
export function countStatements(sql: string): number {
  let count = 0;
  let pending = false;
  for (const token of tokenize(sql)) {
    if (token.kind === "punctuation" && token.text === ";") {
      count += pending ? 1 : 0;
      pending = false;
    } else if (!IGNORED_KINDS.has(token.kind) && token.text.trim().length > 0) {
      pending = true;
    }
  }
  return count + (pending ? 1 : 0);
}
