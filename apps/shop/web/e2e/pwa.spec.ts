import { expect, test, type Page } from "@playwright/test";

import { fakeShop } from "./fakeShop";

async function waitForServiceWorkerControl(page: Page): Promise<void> {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
}

test.describe("before the app loads", () => {
  // Sin service worker, para que el bundle se pida a la red y se pueda retener.
  test.use({ serviceWorkers: "block" });

  test("the remembered theme is already applied", async ({ page, context }) => {
    await fakeShop(context);
    await page.goto("/");
    await page.getByRole("radio", { name: "Tema oscuro" }).check();
    await context.route("**/assets/index-*.js", () => undefined);
    // El bundle es un módulo diferido: retenido, la página nunca llega a DOMContentLoaded.
    await page.reload({ waitUntil: "commit" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#root")).toBeEmpty();
  });
});

test("offline, the shell still opens but no api answer is cached", async ({ page, context }) => {
  await fakeShop(context);
  await page.goto("/");
  await waitForServiceWorkerControl(page);
  await expect(page.getByText("En línea")).toBeVisible();
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
