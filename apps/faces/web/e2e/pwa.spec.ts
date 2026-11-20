import { expect, test, type Page } from "@playwright/test";

async function waitForServiceWorkerControl(page: Page): Promise<void> {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
}

test.beforeEach(async ({ context }) => {
  await context.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", version: "0.1.0" } }));
});

test("installable: the manifest names the app with its icons", async ({ page }) => {
  await page.goto("/");
  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  const manifest = (await (await page.request.get(href ?? "")).json()) as { name: string; display: string; icons: { sizes: string }[] };
  expect(manifest.name).toBe("Rostros");
  expect(manifest.display).toBe("standalone");
  expect(manifest.icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
});

test("offline, the shell still opens but no api answer is cached", async ({ page, context }) => {
  await page.goto("/");
  await waitForServiceWorkerControl(page);
  await expect(page.getByText("Servicio disponible")).toBeVisible();
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

test.describe("before the app loads", () => {
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
