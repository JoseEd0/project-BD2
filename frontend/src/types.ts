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
  statement_index: number | null;
}
