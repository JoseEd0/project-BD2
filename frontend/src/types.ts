export interface ColumnInfo {
  name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  primary_key: boolean;
  indexed_with: string | null;
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

export type CellValue = string | number | boolean | null;

export interface StatementOutcome {
  sql: string;
  message: string;
  affected_rows: number;
  elapsed_ms: number;
  returned_rows: number;
}

export interface QueryResponse {
  statements: StatementOutcome[];
  columns: string[];
  rows: CellValue[][];
  plan: PlanInfo | null;
  message: string;
  affected_rows: number;
  elapsed_ms: number;
  in_transaction: boolean;
}

export interface QueryFailure {
  error: string;
  kind: string;
  line: number | null;
  column: number | null;
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

export interface TableStructure {
  table: string;
  organization: string;
  rows: number;
  storage: HeapStorage | SequentialStorage | TreeStructure;
  indexes: {
    name: string;
    column: string;
    method: string;
    structure: TreeStructure | HashStructure;
  }[];
}
