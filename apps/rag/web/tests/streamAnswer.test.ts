import { describe, expect, it, vi } from "vitest";

import { NO_FILTERS } from "../src/api/types";
import type { AnswerEvent } from "../src/research/answerEvents";
import { AnswerRequestError, streamAnswer } from "../src/research/streamAnswer";
import { answerStream, chunkedBody, routeFetch, sseFrame, sseResponse, splitBytes } from "./api";

async function collect(chunkSize = 7) {
  const events: AnswerEvent[] = [];
  const onOpen = vi.fn();
  await streamAnswer({ question: "¿Qué es un índice aprendido?", filters: NO_FILTERS, history: [] }, { onOpen, onEvent: (event) => events.push(event) }, new AbortController().signal);
  return { events, onOpen, chunkSize };
}

async function failureOf(response: Response | Promise<Response>) {
  routeFetch(() => response);
  try {
    await collect();
  } catch (error) {
    expect(error).toBeInstanceOf(AnswerRequestError);
    return (error as AnswerRequestError).failure;
  }
  throw new Error("no falló");
}

describe("streamAnswer", () => {
  it("posts the question and its filters and delivers every event in order", async () => {
    const requests: Request[] = [];
    routeFetch(async (request) => {
      requests.push(request.clone());
      return sseResponse(answerStream("Los índices aprendidos [1] reemplazan nodos [2]."), 5, { "Server-Timing": "bm25;dur=12, total;dur=20" });
    });
    const { events, onOpen } = await collect();
    expect(onOpen).toHaveBeenCalledWith("bm25;dur=12, total;dur=20");
    const types = events.map((event) => event.type);
    expect(types[0]).toBe("sources");
    expect(types.at(-1)).toBe("done");
    expect(new Set(types.slice(1, -1))).toEqual(new Set(["delta"]));
    const text = events.flatMap((event) => (event.type === "delta" ? [event.text] : [])).join("");
    expect(text).toBe("Los índices aprendidos [1] reemplazan nodos [2].");
    const request = requests[0];
    expect(request?.method).toBe("POST");
    expect(new URL(request?.url ?? "").pathname).toBe("/api/answers");
    expect(await request?.json()).toEqual({ question: "¿Qué es un índice aprendido?", history: [] });
  });

  it("decodes a character whose bytes arrive in different chunks", async () => {
    const body = sseFrame("delta", { text: "ñandú 🙂" }) + sseFrame("done", {});
    for (let size = 1; size <= 4; size += 1) {
      routeFetch(() => new Response(chunkedBody(splitBytes(body, size)), { headers: { "content-type": "text/event-stream" } }));
      const { events } = await collect();
      expect(events[0]).toEqual({ type: "delta", text: "ñandú 🙂" });
    }
  });

  it("ignores events it does not know", async () => {
    routeFetch(() => sseResponse(sseFrame("ping", {}) + sseFrame("delta", { text: "a" })));
    const { events } = await collect();
    expect(events).toEqual([{ type: "delta", text: "a" }]);
  });

  it("maps a missing language model to answers-unavailable", async () => {
    const failure = await failureOf(
      Response.json({ error: "las respuestas no están configuradas", kind: "AnswersUnavailableError", request_id: "r1" }, { status: 503 }),
    );
    expect(failure).toEqual({ kind: "answers-unavailable", detail: "las respuestas no están configuradas", requestId: "r1", retryable: false });
  });

  it("maps the daily budget to budget-exhausted, not retryable", async () => {
    const failure = await failureOf(Response.json({ error: "tope", kind: "BudgetExhaustedError", request_id: null }, { status: 429 }));
    expect(failure.kind).toBe("budget-exhausted");
    expect(failure.retryable).toBe(false);
  });

  it("reads nginx's HTML 429 as rate limited and retryable", async () => {
    const failure = await failureOf(new Response("<html>429 Too Many Requests</html>", { status: 429, headers: { "content-type": "text/html" } }));
    expect(failure).toMatchObject({ kind: "rate-limited", retryable: true, detail: null });
  });

  it("reads a dead engine as engine-unavailable", async () => {
    const failure = await failureOf(Response.json({ error: "el gestor no está", kind: "EngineUnavailableError", request_id: "r2" }, { status: 503 }));
    expect(failure.kind).toBe("engine-unavailable");
  });

  it("reads a fetch that never reaches the server as a network failure", async () => {
    const failure = await failureOf(Promise.reject(new TypeError("Failed to fetch")));
    expect(failure).toMatchObject({ kind: "network", retryable: true });
  });

  it("rethrows the abort when the reader stops the answer", async () => {
    const controller = new AbortController();
    routeFetch((request) => {
      controller.abort();
      return Promise.reject(request.signal.reason ?? new DOMException("cancelada", "AbortError"));
    });
    await expect(streamAnswer({ question: "q", filters: NO_FILTERS, history: [] }, { onOpen: vi.fn(), onEvent: vi.fn() }, controller.signal)).rejects.not.toBeInstanceOf(AnswerRequestError);
  });
});
