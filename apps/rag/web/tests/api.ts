import { vi } from "vitest";

import type { AnswerSource, DoneData } from "../src/research/answerEvents";

/** Un `fetch` de mentira que recibe la petición ya armada, venga como `Request` o como URL e `init`. */
export function routeFetch(respond: (request: Request) => Response | Promise<Response>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), "http://app.test"), init);
      return Promise.resolve(respond(request));
    }),
  );
}

const encoder = new TextEncoder();

/** Un cuerpo SSE entregado en los trozos dados, en bytes: un trozo puede cortar un carácter. */
export function chunkedBody(chunks: readonly Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

export function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** El texto en bytes, cortado cada `size` bytes. */
export function splitBytes(text: string, size: number): Uint8Array[] {
  const bytes = encoder.encode(text);
  const chunks: Uint8Array[] = [];
  for (let start = 0; start < bytes.length; start += size) {
    chunks.push(bytes.slice(start, start + size));
  }
  return chunks;
}

export function sseResponse(text: string, chunkSize = 7, headers: Record<string, string> = {}): Response {
  return new Response(chunkedBody(splitBytes(text, chunkSize)), {
    status: 200,
    headers: { "content-type": "text/event-stream", ...headers },
  });
}

export const PAPER = {
  paper_id: "2505.01180",
  title: "BS-tree: A gapped data-parallel B-tree",
  authors: ["Dimitrios Tsitsigkos", "Achilleas Michalopoulos", "Nikos Mamoulis", "Manolis Terrovitis"],
  year: 2025,
  url: "https://arxiv.org/abs/2505.01180",
  file_path: "pdf/2505.01180.pdf",
  media_type: "application/pdf",
  license: "http://creativecommons.org/licenses/by/4.0/",
};

export function source(n: number, page: number, text = `Learned indexes replace inner nodes, passage ${n}.`): AnswerSource {
  return { n, chunk_id: `2505.01180#${n}`, paper: PAPER, page_start: page, page_end: page, text };
}

export const SOURCES: readonly AnswerSource[] = [source(1, 2), source(2, 1), source(3, 4)];

export function done(answer: string, overrides: Partial<DoneData> = {}): DoneData {
  return {
    model: "claude-sonnet-5-5",
    stop_reason: "end_turn",
    complete: true,
    refusal_category: null,
    answer,
    citations: [],
    invalid_citations: [],
    answered_from_documents: true,
    usage: { input_tokens: 4700, output_tokens: 380, cache_creation_input_tokens: 0, cache_read_input_tokens: 650 },
    attempts: [],
    cost_usd: 0.013265,
    ...overrides,
  };
}

/** Un stream de respuesta completo: fuentes, el texto en deltas de una palabra y el cierre. */
export function answerStream(answer: string, overrides: Partial<DoneData> = {}, sources: readonly AnswerSource[] = SOURCES): string {
  const words = answer.match(/\S+\s*/g) ?? [];
  return (
    sseFrame("sources", { question: "q", model: "claude-sonnet-5-5", sources }) +
    words.map((word) => sseFrame("delta", { text: word })).join("") +
    sseFrame("done", done(answer, overrides))
  );
}

export const FACETS = {
  years: [
    { year: 2025, papers: 88 },
    { year: 2026, papers: 1406 },
  ],
  categories: [
    { category: "cs.DB", papers: 749 },
    { category: "cs.IR", papers: 744 },
  ],
};
