import { expect, test } from "@playwright/test";

// Del corpus diminuto (`apps/rag/tests/tiny_corpus.py`): el fragmento de BM25 va de la
// página 1 a la 2 de un PDF de 3, y el modelo simulado cita los documentos que comparten palabras con
// la pregunta.
const QUESTION = "How does BM25 saturate term frequency?";
const CITED_TITLE = "Ranking Documents with BM25";
const CITED_PAGE = 1;
const PAGES = 3;

test("a question streams a cited answer and the citation opens the pdf at its page", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Pregunta" }).fill(QUESTION);
  const answer = page.waitForResponse((response) => response.url().endsWith("/api/answers"));
  await page.getByRole("button", { name: "Preguntar" }).click();
  expect((await answer).headers()["content-type"]).toContain("text/event-stream");

  await expect(page.getByText(/Respuesta simulada, sin modelo de lenguaje/)).toBeVisible();
  const citation = page.getByRole("button", { name: new RegExp(`^Cita \\d+: ${CITED_TITLE}, pp?\\. ${CITED_PAGE}\\b`) }).first();
  await expect(citation).toBeVisible();
  await citation.click();

  await expect(page.getByRole("heading", { level: 2, name: CITED_TITLE })).toBeVisible();
  await expect(page.getByText(`Página ${CITED_PAGE} de ${PAGES}`)).toBeVisible();
  await expect(page.getByRole("img", { name: `Página ${CITED_PAGE} del PDF` })).toBeVisible();
  await expect(page.locator(".textLayer")).toContainText("BM25 ranks documents by term frequency");
  await expect(page.locator(".textLayer .is-cited").first()).toBeVisible();
});
