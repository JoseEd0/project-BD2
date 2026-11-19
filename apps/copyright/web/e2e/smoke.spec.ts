import { expect, test, type Page } from "@playwright/test";

const SERVICE_TIMING = "parse;dur=0.400, exec;dur=2.500, total;dur=3.500";

async function waitForServiceWorkerControl(page: Page): Promise<void> {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
}

test.beforeEach(async ({ context }) => {
  await context.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok", version: "0.1.0" }, headers: { "Server-Timing": SERVICE_TIMING } }),
  );
});

test("the home page shows the service and where its time went", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Disponible")).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "exec" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "Fuera de las etapas" })).toBeVisible();
});

test.describe("before the app loads", () => {
  // Sin service worker, para que el bundle se pida a la red y se pueda retener.
  test.use({ serviceWorkers: "block" });

  test("the remembered theme is already applied", async ({ page, context }) => {
    await page.goto("/");
    await page.getByText("Oscuro", { exact: true }).click();
    await context.route("**/assets/index-*.js", () => undefined);
    // El bundle es un módulo diferido: retenido, la página nunca llega a DOMContentLoaded.
    await page.reload({ waitUntil: "commit" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#root")).toBeEmpty();
  });
});

test("offline, the shell still opens but no api answer is cached", async ({ page, context }) => {
  await page.goto("/");
  await waitForServiceWorkerControl(page);
  await expect(page.getByText("Disponible")).toBeVisible();
  // Sin rutas simuladas: offline, lo único que podría responder a /api es una caché.
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
  await expect(page.evaluate(() => fetch("/api/health").then((response) => response.status))).rejects.toThrow();
});
