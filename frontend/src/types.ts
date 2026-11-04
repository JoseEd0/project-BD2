export interface ColumnInfo {
  name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  primary_key: boolean;
  indexed_with: string[];
  unique: boolean;
}

export interface TableInfo {
  name: string;
  organization: string;
  primary_key: string | null;
  row_count: number;
  columns: ColumnInfo[];
  indexes: string[];
}

export interface PlanInfo {
  operation: string;
  detail: string;
  children: PlanInfo[];
  actual_rows: number | null;
  actual_ms: number | null;
}

/** Punto geográfico en grados decimales, tal como lo entrega el API. */
export interface GeoPoint {
  lat: number;
  lon: number;
}

export type CellValue = string | number | boolean | null | GeoPoint;

/** Figura de una consulta espacial que el mapa dibuja sobre los puntos. */
export interface Overlay {
  kind: "radius" | "nearest" | "polygon";
  center: GeoPoint | null;
  radius: number | null;
  metric: string | null;
  unit: string | null;
  vertices: GeoPoint[];
}

/** Tabla y columna POINT que toca una consulta, con sus figuras. */
export interface SpatialInfo {
  table: string;
  column: string;
  overlays: Overlay[];
}

/** Puntos de una tabla para el fondo del mapa: pares `[latitud, longitud]`. */
export interface TablePoints {
  table: string;
  column: string;
  total: number;
  points: [number, number][];
}

export interface StatementOutcome {
  sql: string;
  message: string;
  affected_rows: number;
  elapsed_ms: number;
  returned_rows: number;
}

/** Dónde buscó el `MATCH` y con qué términos ya analizados (plegados y reducidos a su raíz). */
export interface TextSearchInfo {
  table: string;
  column: string;
  query: string;
  method: "TF_IDF" | "BM25";
  terms: string[];
  /** Los parámetros con que puntuó BM25: los del `WITH` o los del motor; `null` con TF-IDF. */
  k1: number | null;
  b: number | null;
}

export interface QueryResponse {
  statements: StatementOutcome[];
  columns: string[];
  rows: CellValue[][];
  /** Filas que devolvió la consulta; `rows` trae como mucho las que el API deja viajar. */
  total_rows: number;
  plan: PlanInfo | null;
  message: string;
  affected_rows: number;
  elapsed_ms: number;
  in_transaction: boolean;
  spatial: SpatialInfo | null;
  text: TextSearchInfo | null;
}

export interface QueryFailure {
  error: string;
  kind: string;
  line: number | null;
  column: number | null;
  /** Cómo quedó la sesión tras el error; `null` si el error no pasó por una sesión. */
  in_transaction: boolean | null;
}

export interface FileUpload {
  path: string;
  columns: string[];
}

export interface TreeLevel {
  kind: string;
  node_count: number;
  key_count: number;
  nodes: { keys: string[]; key_count: number }[];
}

export interface TreeStructure {
  kind: "bplustree";
  height: number;
  entries: number;
  pages: number;
  leaf_capacity: number;
  internal_capacity: number;
  levels: TreeLevel[];
}

export interface HashBucket {
  bits: string;
  local_depth: number;
  entries: number;
  pointers: number;
  overflow_pages: number;
}

export interface HashStructure {
  kind: "hash";
  global_depth: number;
  directory_size: number;
  bucket_capacity: number;
  bucket_count: number;
  entries: number;
  buckets: HashBucket[];
}

/** MBR de un nodo: `[lat mínima, lon mínima, lat máxima, lon máxima]`. */
export type Bounds = [number, number, number, number];

export interface RTreeLevel {
  kind: string;
  node_count: number;
  entry_count: number;
  nodes: { bounds: Bounds | null; entry_count: number }[];
}

export interface RTreeStructure {
  kind: "rtree";
  height: number;
  entries: number;
  nodes: number;
  pages: number;
  leaf_capacity: number;
  branch_capacity: number;
  minimum_fill: number;
  levels: RTreeLevel[];
}

export interface InvertedTerm {
  term: string;
  document_frequency: number;
  total_frequency: number;
}

/** Un segmento del índice en disco: lo que dejó un bloque de SPIMI o una fusión. */
export interface InvertedSegment {
  generation: number;
  terms: number;
  postings: number;
  blocks: number;
  merge_passes: number;
}

export interface InvertedStructure {
  kind: "inverted";
  language: string;
  documents: number;
  /** Documentos borrados con lápida; los postings de algunos siguen en disco hasta fusionar. */
  deleted: number;
  deleted_with_postings: number;
  total_length: number;
  average_length: number;
  vocabulary: number;
  postings: number;
  /** Bloques de SPIMI escritos a disco, y fusiones de segmentos hechas. */
  blocks: number;
  merges: number;
  segments: InvertedSegment[];
  /** El bloque de SPIMI que todavía vive en memoria. */
  pending: { documents: number; terms: number; postings: number; estimated_bytes: number };
  term_bytes: number;
  postings_bytes: number;
  /** Bytes en disco de cada archivo del índice: léxico, términos, postings, documentos… */
  file_bytes: Record<string, number>;
  top_terms: InvertedTerm[];
}

export type IndexStructure = TreeStructure | HashStructure | RTreeStructure | InvertedStructure;

export interface HeapStorage {
  kind: "heap";
  pages: number;
  slots_per_page: number;
  records: number;
}

export interface SequentialStorage {
  kind: "sequential";
  main_pages: number;
  slots_per_page: number;
  records: number;
  overflow_records: number;
  deleted_records: number;
  waste_ratio: number;
}

/** El archivo donde viven los valores de las columnas TEXT de una tabla. */
export interface TextValuesStorage {
  kind: "varlen";
  pages: number;
  data_pages: number;
  map_pages: number;
  values: number;
  value_bytes: number;
  inline_limit: number;
  chunk_size: number;
}

export interface TableStructure {
  table: string;
  organization: string;
  rows: number;
  storage: HeapStorage | SequentialStorage | TreeStructure;
  text_values: TextValuesStorage | null;
  indexes: {
    name: string;
    column: string;
    method: string;
    structure: IndexStructure;
  }[];
}
