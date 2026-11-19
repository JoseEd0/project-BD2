import { jsonResponse } from "@minigestor/web-kit/testing";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PdfPageProps } from "../src/reader/PdfPage";
import { answerStream, FACETS, PAPER, routeFetch, sseFrame, sseResponse, SOURCES } from "./api";
import { renderAt } from "./render";

// pdf.js necesita un canvas y un worker de verdad: su página se prueba en Playwright.
vi.mock("../src/reader/PdfPage", () => ({
  PdfPage: ({ page, passage }: PdfPageProps) => (
    <p>
      PDF en la página {page}
      {passage === null ? "" : `, con el pasaje marcado`}
    </p>
  ),
}));

const QUESTION = "¿Cómo reemplazan los índices aprendidos a los árboles B?";
const ANSWER = "Los índices aprendidos reemplazan los nodos internos por modelos [1]. Un B+ los compara [2][9].";

const SEARCH = {
  query: QUESTION,
  mode: "hybrid",
  results: [
    {
      rank: 1,
      chunk_id: SOURCES[0]?.chunk_id,
      paper: PAPER,
      page_start: 2,
      page_end: 2,
      text: "Learned indexes replace inner nodes.",
      score: 0.032,
      bm25: { rank: 1, score: 12.5 },
      vector: { rank: 3, score: 0.84 },
    },
  ],
  queries: [
    { stage: "bm25", sql: "SELECT chunk_id FROM rag_chunks WHERE MATCH(content, $1) USING BM25 ORDER BY score DESC LIMIT $2", parameters: ['"q"', "50"] },
  ],
};

interface Api {
  readonly answers: (request: Request) => Response | Promise<Response>;
  readonly requests: Request[];
}

function api(answers: Api["answers"]): Api {
  const requests: Request[] = [];
  routeFetch((request) => {
    requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/health") {
      return jsonResponse({ status: "ok", version: "0.1.0" });
    }
    if (path === "/api/facets") {
      return jsonResponse(FACETS);
    }
    if (path === "/api/answers") {
      return answers(request);
    }
    if (path === "/api/search") {
      return jsonResponse(SEARCH, { headers: { "Server-Timing": "embedding;dur=10, bm25;dur=20, total;dur=40" } });
    }
    if (path.startsWith("/api/papers/")) {
      const page = Number(path.split("/").at(-1));
      return jsonResponse({ paper: PAPER, page, pages: 14, file_url: `/papers/${PAPER.paper_id}/file`, viewer_url: "", passages: [] });
    }
    return new Response(null, { status: 404 });
  });
  return { answers, requests };
}

async function ask(question = QUESTION) {
  const user = userEvent.setup();
  renderAt("/");
  await user.type(await screen.findByRole("textbox", { name: "Pregunta" }), `${question}{Enter}`);
  return user;
}

describe("asking a question", () => {
  it("streams the answer, links each valid citation and strikes the invalid one", async () => {
    api(() => sseResponse(answerStream(ANSWER, { invalid_citations: [9] })));
    await ask();
    const answer = await screen.findByRole("article", { name: QUESTION });
    await within(answer).findByText(/Algunas citas no se pueden|Una cita no se puede/);
    expect(within(answer).getByRole("button", { name: /^Cita 1: BS-tree/ })).toBeTruthy();
    expect(within(answer).getByRole("button", { name: /^Cita 2: BS-tree/ })).toBeTruthy();
    expect(within(answer).queryByRole("button", { name: /^Cita 9/ })).toBeNull();
    expect(within(answer).getByText("[9] no corresponde", { exact: false })).toBeTruthy();
    expect(within(answer).getByText(/claude-sonnet-5-5 · primer texto/)).toBeTruthy();
    expect(within(answer).getByText(/0,0133/)).toBeTruthy();
  });

  it("sends the chosen filters with the question", async () => {
    const { requests } = api(() => sseResponse(answerStream(ANSWER)));
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("checkbox", { name: /cs\.IR/ }));
    await user.selectOptions(screen.getByLabelText("Desde"), "2026");
    await user.type(screen.getByRole("textbox", { name: "Pregunta" }), `${QUESTION}{Enter}`);
    await screen.findByText(/Respuesta|reemplazan los nodos/);
    const posted = requests.find((request) => new URL(request.url).pathname === "/api/answers");
    expect(await posted?.json()).toEqual({ question: QUESTION, filters: { year_from: 2026, year_to: null, categories: ["cs.IR"] } });
  });

  it("opens the cited page of the PDF beside the answer", async () => {
    api(() => sseResponse(answerStream(ANSWER)));
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: /^Cita 1: BS-tree/ }));
    expect(await screen.findByRole("heading", { level: 2, name: PAPER.title })).toBeTruthy();
    expect(await screen.findByText("PDF en la página 2, con el pasaje marcado")).toBeTruthy();
    expect(screen.getByText(SOURCES[0]?.text ?? "")).toBeTruthy();
    const frames = screen.getByRole("navigation", { name: "Páginas del paper" });
    expect(within(frames).getByRole("button", { name: "Página 2, citada, del fragmento" }).getAttribute("aria-current")).toBe("page");
    expect(within(frames).getByRole("button", { name: "Página 1, citada" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Página siguiente" }));
    expect(await screen.findByText("PDF en la página 3")).toBeTruthy();
  });

  it("shows each retrieval method with its SQL and timings", async () => {
    api(() => sseResponse(answerStream(ANSWER)));
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Recuperación" }));
    expect(await screen.findByText(/MATCH\(content, \$1\)/)).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Fusión RRF/ })).toHaveProperty("checked", true);
    expect(screen.getByRole("rowheader", { name: "BM25" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Learned indexes replace inner nodes/ })).toBeTruthy();
  });

  it("explains that answers are off when the service has no model key, and offers the chunks", async () => {
    api(() => Response.json({ error: "falta la clave", kind: "AnswersUnavailableError", request_id: "r1" }, { status: 503 }));
    const user = await ask();
    expect(await screen.findByText("Las respuestas no están activas en este servidor")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Ver los fragmentos" }));
    expect(await screen.findByRole("tab", { name: "Recuperación", selected: true })).toBeTruthy();
  });

  it("says the daily budget ran out", async () => {
    api(() => Response.json({ error: "tope", kind: "BudgetExhaustedError", request_id: null }, { status: 429 }));
    await ask();
    expect(await screen.findByText("Se alcanzó el gasto máximo de hoy")).toBeTruthy();
  });

  it("lets the reader retry after a rate limit and asks the same question again", async () => {
    let calls = 0;
    api(() => {
      calls += 1;
      return calls === 1 ? new Response("<html>429</html>", { status: 429 }) : sseResponse(answerStream(ANSWER));
    });
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("button", { name: /^Cita 1: BS-tree/ })).toBeTruthy();
    expect(calls).toBe(2);
  });

  it("marks an answer cut by the length limit as incomplete", async () => {
    api(() => sseResponse(answerStream("Los índices aprendidos [1] y", { complete: false, stop_reason: "max_tokens" })));
    await ask();
    expect(await screen.findByText("Respuesta incompleta: llegó al tope de longitud")).toBeTruthy();
  });

  it("says when the documents do not answer", async () => {
    api(() => sseResponse(answerStream("Los documentos no responden esto.", { answered_from_documents: false })));
    await ask();
    expect(await screen.findByText("Los documentos no responden esta pregunta")).toBeTruthy();
  });

  it("says no chunk matched when retrieval finds nothing", async () => {
    api(() => sseResponse(sseFrame("sources", { question: "q", model: "m", sources: [] }) + sseFrame("done", { answer: "No hay documentos.", citations: [], invalid_citations: [], answered_from_documents: false, complete: true, stop_reason: "end_turn", usage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }, attempts: [], cost_usd: 0, model: "m", refusal_category: null })));
    await ask();
    expect(await screen.findByText("Ningún fragmento del corpus coincide")).toBeTruthy();
  });

  it("reports a model failure in the middle of the stream", async () => {
    api(() => sseResponse(answerStream("").replace(/event: done[\s\S]*$/, "") + sseFrame("delta", { text: "Empieza" }) + sseFrame("error", { error: "sobrecargado", kind: "LlmUnavailableError", request_id: "r9" })));
    await ask();
    expect(await screen.findByText("El modelo no respondió")).toBeTruthy();
    expect(screen.getByText("Petición r9")).toBeTruthy();
  });

  it("stops a streaming answer when asked", async () => {
    api(
      (request) =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(sseFrame("sources", { question: "q", model: "m", sources: SOURCES }) + sseFrame("delta", { text: "Escribiendo" })));
              request.signal.addEventListener("abort", () => controller.error(new DOMException("cancelada", "AbortError")));
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        ),
    );
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Detener" }));
    await waitFor(() => expect(screen.getByText("Detuviste la respuesta; el texto de arriba está incompleto.")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Preguntar" })).toBeTruthy();
  });
});
