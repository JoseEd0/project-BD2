import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const APPS = [
  { slug: "copyright", name: "Copyright en audio", nav: "Copyright" },
  { slug: "tienda", name: "Tienda con recomendaciones", nav: "Tienda" },
  { slug: "papers", name: "Preguntas a papers", nav: "Papers" },
  { slug: "rostros", name: "Reconocimiento de rostros", nav: "Rostros" },
];
const ENGINE_TIMING = "parse;dur=0.400, plan;dur=0.100, exec;dur=2.500, total;dur=3.500";
const MOBILE_VIEWPORT = { width: 390, height: 844 };

/** El gestor responde y ninguna aplicación está desplegada: el estado real de hoy. */
async function onlyEngineDeployed(context: BrowserContext): Promise<void> {
  await context.route("**/api/health", (route) =>
    route.fulfill({
      json: { status: "ok", version: "0.1.0" },
      headers: { "Server-Timing": ENGINE_TIMING },
    }),
  );
  await context.route("**/apps/**", (route) => route.abort("connectionrefused"));
}

async function waitForServiceWorkerControl(page: Page): Promise<void> {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
}

test.beforeEach(async ({ context }) => {
  await onlyEngineDeployed(context);
});

test("the home page shows every app and each service's status", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Aplicaciones del Minigestor");
  const lanes = page.getByRole("list", { name: "Aplicaciones y estado de su servicio" }).getByRole("listitem");
  await expect(lanes).toHaveCount(APPS.length);
  await expect(lanes.getByText("No disponible")).toHaveCount(APPS.length);
  await expect(page.getByRole("region", { name: "Gestor de base de datos" }).getByText("Disponible")).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "Ejecutar" })).toBeVisible();
  await expect(page.getByText("3,50 ms")).toBeVisible();
});

test("every app route opens from the bar with its empty state", async ({ page }) => {
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Aplicaciones" });
  for (const app of APPS) {
    await navigation.getByRole("link", { name: app.nav }).click();
    await expect(page).toHaveURL(`/${app.slug}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(app.name);
    await expect(page.getByText(/no está desplegado en este entorno/)).toBeVisible();
    await expect(page).toHaveTitle(`${app.name} | Aplicaciones del Minigestor`);
  }
});

test("the portal works from the keyboard with a visible focus", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Saltar al contenido" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  const outline = await skip.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).toBe("solid");
  await page.getByRole("radio", { name: "Sistema" }).focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("radio", { name: "Oscuro" })).toBeChecked();
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

test.describe("on a phone", () => {
  test.use({ viewport: MOBILE_VIEWPORT });

  test("the apps sit in a bottom bar without horizontal scroll", async ({ page }) => {
    await page.goto("/");
    const navigation = page.getByRole("navigation", { name: "Aplicaciones" });
    const box = await navigation.boundingBox();
    expect(box && box.y + box.height).toBeCloseTo(MOBILE_VIEWPORT.height, 0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});

/**
 * Lo que Chrome pide para instalar: un manifiesto que analiza sin errores, con nombre,
 * `display: standalone` e iconos de 192 y 512 que cargan, y un service worker que controla
 * la página (el test de abajo comprueba que además responde sin red). Se mira con el
 * analizador del propio Chrome; `Page.getInstallabilityErrors` ya no sirve: devuelve una
 * lista vacía aunque el manifiesto esté roto.
 */
test("Chrome parses a manifest that makes the portal installable", async ({ page }) => {
  await page.goto("/");
  await waitForServiceWorkerControl(page);
  const session = await page.context().newCDPSession(page);
  const { errors, manifest } = await session.send("Page.getAppManifest");
  expect(errors).toEqual([]);
  expect(manifest).toMatchObject({ name: "Aplicaciones del Minigestor", display: "kStandalone" });
  const icons = manifest?.icons ?? [];
  expect(icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  for (const icon of icons) {
    const response = await page.request.get(icon.url);
    expect(response.headers()["content-type"]).toBe("image/png");
  }
});

test("offline, the shell still opens every route but no service answer is cached", async ({ page, context }) => {
  await page.goto("/");
  await waitForServiceWorkerControl(page);
  await expect(page.getByRole("region", { name: "Gestor de base de datos" }).getByText("Disponible")).toBeVisible();
  // Sin rutas simuladas: offline, lo único que podría responder a /api es una caché.
  await context.unrouteAll();
  await context.setOffline(true);
  await page.goto("/tienda");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tienda con recomendaciones");
  await expect(page.getByText(/Sin conexión/)).toBeVisible();
  const cachedEngine = await page.evaluate(async () => {
    const names = await caches.keys();
    const hits = await Promise.all(names.map((name) => caches.open(name).then((cache) => cache.keys())));
    return hits.flat().filter((request) => new URL(request.url).pathname.startsWith("/api")).length;
  });
  expect(cachedEngine).toBe(0);
  await expect(page.evaluate(() => fetch("/api/health").then((response) => response.status))).rejects.toThrow();
});
