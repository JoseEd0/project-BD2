import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { pdfBytes } from "./pdf";

const PAPER = {
  paper_id: "2505.01180",
  title: "BS-tree: A gapped data-parallel B-tree",
  authors: ["Dimitrios Tsitsigkos", "Nikos Mamoulis"],
  year: 2025,
  url: "https://arxiv.org/abs/2505.01180",
  file_path: "pdf/2505.01180.pdf",
  media_type: "application/pdf",
  license: "http://creativecommons.org/licenses/by/4.0/",
};

const PAGES = [
  ["Introduction", "The B+-tree is the dominant indexing method for databases."],
  ["Learned Indexing", "Learned indexes replace the inner nodes of the B+-tree", "by a hierarchy of models that predict the key position.", "Other structures follow in the next section."],
  ["Experiments", "We compare against ALEX and PGM-index."],
];
const CITED_PAGE = 2;
const CITED_TEXT = "Learned indexes replace the inner nodes of the B+-tree by a hierarchy of models that predict the key position.";
const QUESTION = "How do learned indexes replace B-trees?";
const ANSWER_WORDS = ["Learned ", "indexes ", "replace ", "inner ", "nodes ", "with ", "models ", "[1]."];

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const SSE_BODY =
  frame("sources", {
    question: QUESTION,
    model: "simulated",
    sources: [{ n: 1, chunk_id: "2505.01180#3", paper: PAPER, page_start: CITED_PAGE, page_end: CITED_PAGE, text: CITED_TEXT }],
  }) +
  ANSWER_WORDS.map((text) => frame("delta", { text })).join("") +
  frame("done", {
    model: "simulated",
    stop_reason: "end_turn",
    complete: true,
    refusal_category: null,
    answer: ANSWER_WORDS.join(""),
    citations: [{ n: 1, chunk_id: "2505.01180#3", paper_id: PAPER.paper_id, title: PAPER.title, page_start: CITED_PAGE, page_end: CITED_PAGE, count: 1 }],
    invalid_citations: [],
    answered_from_documents: true,
    usage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
    attempts: [],
    cost_usd: 0,
  });

/** El API del servicio con el LLM simulado: lo que respondería `RAG_LLM_PROVIDER=simulated`. */
async function simulatedApi(context: BrowserContext): Promise<void> {
  await context.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", version: "0.1.0" } }));
  await context.route("**/api/facets", (route) =>
    route.fulfill({ json: { years: [{ year: 2025, papers: 1 }], categories: [{ category: "cs.DB", papers: 1 }] } }),
  );
  await context.route("**/api/answers", (route) =>
    route.fulfill({ status: 200, headers: { "content-type": "text/event-stream", "server-timing": "bm25;dur=12, vector;dur=8, total;dur=25" }, body: SSE_BODY }),
  );
  await context.route("**/api/search", (route) => route.fulfill({ json: { query: QUESTION, mode: "hybrid", results: [], queries: [] } }));
  await context.route(/\/api\/papers\/[^/]+\/pages\/\d+$/, (route) => {
    const page = Number(new URL(route.request().url()).pathname.split("/").at(-1));
    return route.fulfill({
      json: { paper: PAPER, page, pages: PAGES.length, file_url: `/papers/${PAPER.paper_id}/file`, viewer_url: `/papers/${PAPER.paper_id}/file#page=${page}`, passages: [] },
    });
  });
  await context.route("**/api/papers/*/file", (route) =>
    route.fulfill({ status: 200, headers: { "content-type": "application/pdf" }, body: pdfBytes(PAGES) }),
  );
}

async function askAndCite(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Pregunta" }).fill(QUESTION);
  await page.getByRole("button", { name: "Preguntar" }).click();
  const citation = page.getByRole("button", { name: `Cita 1: ${PAPER.title}, p. ${CITED_PAGE}` });
  await expect(citation).toBeVisible();
  await expect(page.getByText("Learned indexes replace inner nodes with models")).toBeVisible();
  await citation.click();
}

test.beforeEach(async ({ context }) => {
  await simulatedApi(context);
});

test("ask, stream the answer, open the citation: the PDF opens at its page with the passage marked", async ({ page }) => {
  await askAndCite(page);
  await expect(page.getByRole("heading", { level: 2, name: PAPER.title })).toBeVisible();
  await expect(page.getByText(`Página ${CITED_PAGE} de ${PAGES.length}`)).toBeVisible();
  const frames = page.getByRole("navigation", { name: "Páginas del paper" });
  await expect(frames.getByRole("button", { name: /^Página 2,/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("img", { name: `Página ${CITED_PAGE} del PDF` })).toBeVisible();
  await expect(page.locator(".textLayer .is-cited").first()).toBeVisible();
  await expect(page.locator(".textLayer .is-cited")).toContainText(["Learned indexes replace"]);
  await expect(page.locator(".textLayer")).not.toContainText("The B+-tree is the dominant");
  await page.getByRole("button", { name: "Página siguiente" }).click();
  await expect(page.getByText(`Página 3 de ${PAGES.length}`)).toBeVisible();
  await expect(page.locator(".textLayer")).toContainText("We compare against ALEX");
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the page opens in a sheet that closes with Escape", async ({ page }) => {
    await askAndCite(page);
    const sheet = page.getByRole("dialog", { name: "Mesa de trabajo" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText(`Página ${CITED_PAGE} de ${PAGES.length}`)).toBeVisible();
    await expect(sheet.locator(".textLayer .is-cited").first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });
});

test("a service without a model key says so and offers the retrieved chunks", async ({ page, context }) => {
  await context.route("**/api/answers", (route) =>
    route.fulfill({ status: 503, json: { error: "falta la clave", kind: "AnswersUnavailableError", request_id: "r1" } }),
  );
  await page.goto("/");
  await page.getByRole("textbox", { name: "Pregunta" }).fill(QUESTION);
  await page.keyboard.press("Enter");
  await expect(page.getByText("Las respuestas no están activas en este servidor")).toBeVisible();
  await page.getByRole("button", { name: "Ver los fragmentos" }).click();
  await expect(page.getByRole("tab", { name: "Recuperación" })).toHaveAttribute("aria-selected", "true");
});

test.describe("before the app loads", () => {
  // Sin service worker, para que el bundle se pida a la red y se pueda retener.
  test.use({ serviceWorkers: "block" });

  test("the remembered theme is already applied", async ({ page, context }) => {
    await page.goto("/");
    await page.getByRole("radio", { name: "Oscuro" }).check();
    await context.route("**/assets/index-*.js", () => undefined);
    await page.reload({ waitUntil: "commit" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#root")).toBeEmpty();
  });
});

test("offline, the shell still opens but no api answer is cached", async ({ page, context }) => {
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
  await context.unrouteAll();
  await context.setOffline(true);
  await page.goto("/otra-ruta");
  await expect(page.getByRole("heading", { name: "Esta dirección no existe" })).toBeVisible();
  const cachedApi = await page.evaluate(async () => {
    const names = await caches.keys();
    const hits = await Promise.all(names.map((name) => caches.open(name).then((cache) => cache.keys())));
    return hits.flat().filter((request) => new URL(request.url).pathname.startsWith("/api")).length;
  });
  expect(cachedApi).toBe(0);
});
