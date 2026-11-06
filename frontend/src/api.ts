import { API_URL, MISSING_API_URL } from "./config";
import type {
  FileUpload,
  MediaUpload,
  QueryFailure,
  QueryResponse,
  TableInfo,
  TablePoints,
  TableStructure,
} from "./types";

export class ApiError extends Error {
  readonly failure: QueryFailure;

  constructor(failure: QueryFailure) {
    super(failure.error);
    this.name = "ApiError";
    this.failure = failure;
  }
}

/** Cualquier error que el API no devolvió: el servidor está caído o no se configuró. */
export class ConnectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConnectionError";
  }
}

const CONNECTION_FAILED = "No se pudo contactar con el motor. ¿Está el API en marcha?";

function apiUrl(path: string): string {
  if (API_URL === undefined || API_URL.length === 0) {
    throw new ConnectionError(MISSING_API_URL);
  }
  return `${API_URL}${path}`;
}

/** Una petición al API: su cuerpo JSON, o el error que el API explicó. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (error) {
    throw error instanceof ConnectionError ? error : new ConnectionError(CONNECTION_FAILED);
  }
  if (!response.ok) {
    throw new ApiError(await failureOf(response));
  }
  return (await response.json()) as T;
}

async function failureOf(response: Response): Promise<QueryFailure> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;
  return {
    error: detail?.error ?? `El servidor respondió ${response.status}`,
    kind: detail?.kind ?? "HttpError",
    line: detail?.line ?? null,
    column: detail?.column ?? null,
    in_transaction: detail?.in_transaction ?? null,
  };
}

function withSession(path: string, sessionId: string, extra: Record<string, string> = {}): string {
  const query = new URLSearchParams({ ...extra, session_id: sessionId });
  return `${path}?${query}`;
}

export function fetchTables(): Promise<TableInfo[]> {
  return request("/tables");
}

export function runQuery(sql: string, sessionId: string): Promise<QueryResponse> {
  return request("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql, session_id: sessionId }),
  });
}

export interface UploadRequest {
  file: File;
  name: string;
  organization: string;
  keyColumn: string | null;
  sessionId: string;
}

export function uploadCsv(upload: UploadRequest): Promise<QueryResponse> {
  const form = new FormData();
  form.append("file", upload.file);
  form.append("name", upload.name);
  form.append("organization", upload.organization);
  form.append("session_id", upload.sessionId);
  if (upload.keyColumn !== null) {
    form.append("key_column", upload.keyColumn);
  }
  return request("/tables/upload", { method: "POST", body: form });
}

export function dropAllTables(sessionId: string): Promise<QueryResponse> {
  return request(withSession("/tables", sessionId), { method: "DELETE" });
}

export function uploadFileOnly(file: File, name: string): Promise<FileUpload> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  return request("/files/upload", { method: "POST", body: form });
}

/** Guarda la imagen o el audio de una consulta y devuelve la ruta que va en `SIMILAR_TO`. */
export function uploadMediaQuery(file: File): Promise<MediaUpload> {
  const form = new FormData();
  form.append("file", file);
  return request("/media/queries", { method: "POST", body: form });
}

/** Dirección de un archivo multimedia tal cual, para un `<audio>` (acepta `Range`). */
export function mediaFileUrl(path: string): string {
  return apiUrl(`/media/file?${new URLSearchParams({ path })}`);
}

/** Dirección de la miniatura de una imagen. */
export function thumbnailUrl(path: string): string {
  return apiUrl(`/media/thumbnail?${new URLSearchParams({ path })}`);
}

/** Estructura física de la tabla, leída dentro de la sesión: si tiene una transacción que
 * escribe en esa tabla, ve sus cambios en vez de esperar a que termine. */
export function fetchStructure(table: string, sessionId: string): Promise<TableStructure> {
  return request(withSession(`/tables/${encodeURIComponent(table)}/structure`, sessionId));
}

export function fetchTablePoints(
  table: string,
  column: string,
  sessionId: string,
): Promise<TablePoints> {
  return request(withSession(`/tables/${encodeURIComponent(table)}/points`, sessionId, { column }));
}

/**
 * Cierra la sesión al abandonar la página. `sendBeacon` es la única petición que el
 * navegador garantiza enviar mientras descarga la página; un `fetch` normal se cancela y la
 * sesión se quedaría con sus bloqueos hasta expirar en el servidor.
 */
export function closeSession(sessionId: string): void {
  if (API_URL !== undefined && API_URL.length > 0) {
    navigator.sendBeacon(apiUrl(`/sessions/${encodeURIComponent(sessionId)}/close`));
  }
}
