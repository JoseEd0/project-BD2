import { REQUEST_ID_HEADER, SERVER_TIMING_HEADER } from "@minigestor/web-kit";

import { apiUrl } from "../api/client";
import { toRequestFilters, type ResearchFilters } from "../api/types";
import { decodeAnswerEvent, type AnswerEvent } from "./answerEvents";
import { failureOfResponse, networkFailure, type Failure } from "./failures";
import type { Exchange } from "./turns";
import { SseParser, type ServerSentEvent } from "./sse";

export interface AnswerStreamHandlers {
  /** Llega una vez, con las cabeceras, antes del primer evento. */
  readonly onOpen: (serverTiming: string | null) => void;
  readonly onEvent: (event: AnswerEvent) => void;
}

export interface AnswerQuestion {
  readonly question: string;
  readonly filters: ResearchFilters;
  /** Los turnos anteriores, del más viejo al más nuevo. */
  readonly history: readonly Exchange[];
}

export class AnswerRequestError extends Error {
  constructor(readonly failure: Failure) {
    super(failure.detail ?? failure.kind);
    this.name = "AnswerRequestError";
  }
}

async function errorBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    // nginx responde su 429 en HTML: sin cuerpo JSON, decide el estado.
    return null;
  }
}

/**
 * Pide la respuesta a `question` y entrega cada evento del SSE en cuanto llega. Termina
 * cuando el cuerpo se cierra; `signal` lo corta antes.
 *
 * Throws: `AnswerRequestError` si el servicio responde un error antes del stream o la red
 * falla; `DOMException` `AbortError` si se cancela.
 */
export async function streamAnswer(
  { question, filters, history }: AnswerQuestion,
  handlers: AnswerStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiUrl("/answers"), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        [REQUEST_ID_HEADER]: crypto.randomUUID(),
      },
      body: JSON.stringify({ question, filters: toRequestFilters(filters), history }),
      signal,
    });
  } catch (error) {
    if (signal.aborted) {
      throw error;
    }
    throw new AnswerRequestError(networkFailure());
  }
  if (!response.ok || response.body === null) {
    throw new AnswerRequestError(failureOfResponse(response.status, await errorBody(response)));
  }
  handlers.onOpen(response.headers.get(SERVER_TIMING_HEADER));
  await readEvents(response.body, handlers.onEvent, signal);
}

function dispatch(events: readonly ServerSentEvent[], onEvent: (event: AnswerEvent) => void): void {
  for (const raw of events) {
    const event = decodeAnswerEvent(raw.event, raw.data);
    if (event !== null) {
      onEvent(event);
    }
  }
}

async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: AnswerEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const cancel = () => void reader.cancel().catch(() => undefined);
  signal.addEventListener("abort", cancel, { once: true });
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        dispatch(parser.push(decoder.decode()), onEvent);
        dispatch(parser.finish(), onEvent);
        return;
      }
      dispatch(parser.push(decoder.decode(value, { stream: true })), onEvent);
    }
  } catch {
    if (signal.aborted) {
      throw new DOMException("cancelada", "AbortError");
    }
    throw new AnswerRequestError(networkFailure());
  } finally {
    signal.removeEventListener("abort", cancel);
  }
}
