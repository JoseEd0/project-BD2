import type {
  FileUpload,
  QueryFailure,
  QueryResponse,
  TableInfo,
  TableStructure,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly failure: QueryFailure;

  constructor(failure: QueryFailure) {
    super(failure.error);
    this.name = "ApiError";
    this.failure = failure;
  }
}

async function parseFailure(response: Response): Promise<never> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;
  throw new ApiError({
    error: detail?.error ?? `El servidor respondió ${response.status}`,
    kind: detail?.kind ?? "HttpError",
    line: detail?.line ?? null,
    column: detail?.column ?? null,
  });
}

export async function fetchTables(): Promise<TableInfo[]> {
  const response = await fetch(`${API_URL}/tables`);
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export async function runQuery(sql: string, sessionId: string): Promise<QueryResponse> {
  const response = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql, session_id: sessionId }),
  });
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export interface UploadRequest {
  file: File;
  name: string;
  organization: string;
  keyColumn: string | null;
  sessionId: string;
}

export async function uploadCsv(request: UploadRequest): Promise<QueryResponse> {
  const form = new FormData();
  form.append("file", request.file);
  form.append("name", request.name);
  form.append("organization", request.organization);
  form.append("session_id", request.sessionId);
  if (request.keyColumn !== null) {
    form.append("key_column", request.keyColumn);
  }
  const response = await fetch(`${API_URL}/tables/upload`, { method: "POST", body: form });
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export async function dropAllTables(sessionId: string): Promise<QueryResponse> {
  const response = await fetch(`${API_URL}/tables?session_id=${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export async function uploadFileOnly(file: File, name: string): Promise<FileUpload> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  const response = await fetch(`${API_URL}/files/upload`, { method: "POST", body: form });
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export async function fetchStructure(table: string): Promise<TableStructure> {
  const response = await fetch(`${API_URL}/tables/${encodeURIComponent(table)}/structure`);
  if (!response.ok) {
    return parseFailure(response);
  }
  return response.json();
}

export async function closeSession(sessionId: string): Promise<void> {
  await fetch(`${API_URL}/sessions/${sessionId}`, { method: "DELETE" });
}
