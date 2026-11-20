import type { Paper } from "../api/types";

/**
 * Los eventos de `POST /answers` (ver `apps/rag/README.md`, «Los eventos»). El contrato
 * OpenAPI no describe el cuerpo de un SSE, así que sus tipos viven aquí.
 */
export interface AnswerSource {
  readonly n: number;
  readonly chunk_id: string;
  readonly paper: Paper;
  readonly page_start: number;
  readonly page_end: number;
  readonly text: string;
}

export interface SourcesData {
  readonly question: string;
  /** Lo que se buscó en el corpus: la consulta reescrita (en inglés con los términos de los
   * papers, o con el tema de una repregunta) o la pregunta tal cual. */
  readonly search_query?: string;
  readonly model: string;
  readonly sources: readonly AnswerSource[];
}

export interface Citation {
  readonly n: number;
  readonly chunk_id: string;
  readonly paper_id: string;
  readonly title: string;
  readonly page_start: number;
  readonly page_end: number;
  readonly count: number;
}

export interface TokenUsage {
  readonly input_tokens: number;
  readonly output_tokens: number;
  readonly cache_creation_input_tokens: number;
  readonly cache_read_input_tokens: number;
}

export interface AttemptData {
  readonly model: string;
  readonly usage: TokenUsage;
  readonly cost_usd: number | null;
}

export type StopReason = "end_turn" | "max_tokens" | "refusal" | "stop_sequence" | "pause_turn" | string;

export interface DoneData {
  readonly model: string;
  readonly stop_reason: StopReason;
  readonly complete: boolean;
  readonly refusal_category: string | null;
  readonly answer: string;
  readonly citations: readonly Citation[];
  readonly invalid_citations: readonly number[];
  readonly answered_from_documents: boolean;
  /** Un saludo o una pregunta sobre la app: se respondió sin buscar en el corpus. */
  readonly conversational?: boolean;
  readonly usage: TokenUsage;
  readonly attempts: readonly AttemptData[];
  readonly cost_usd: number | null;
}

export interface FallbackData {
  readonly from_model: string;
  readonly to_model: string;
}

export interface ErrorData {
  readonly error: string;
  readonly kind: string;
  readonly request_id: string | null;
}

export type AnswerEvent =
  | { readonly type: "sources"; readonly data: SourcesData }
  | { readonly type: "delta"; readonly text: string }
  | { readonly type: "fallback"; readonly data: FallbackData }
  | { readonly type: "done"; readonly data: DoneData }
  | { readonly type: "error"; readonly data: ErrorData };

/** Un evento del SSE ya interpretado, o `null` si es de otro tipo (se ignora, como manda SSE). */
export function decodeAnswerEvent(name: string, data: string): AnswerEvent | null {
  switch (name) {
    case "sources":
      return { type: "sources", data: JSON.parse(data) as SourcesData };
    case "delta":
      return { type: "delta", text: (JSON.parse(data) as { text: string }).text };
    case "fallback":
      return { type: "fallback", data: JSON.parse(data) as FallbackData };
    case "done":
      return { type: "done", data: JSON.parse(data) as DoneData };
    case "error":
      return { type: "error", data: JSON.parse(data) as ErrorData };
    default:
      return null;
  }
}
