import { expect, test, type Page } from "@playwright/test";

import { DETECTION_TIMING, mockApi, READY_CATALOG, remixFile, REMIX_DETECTION, respondWith } from "./fixtures";

async function upload(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Audio para analizar").setInputFiles(remixFile());
}

test.describe("with the service up", () => {
  test.use({ serviceWorkers: "block" });

  test("a remix of the sample catalog is marked, explained and listened to a/b", async ({ page, context }) => {
    await mockApi(context, respondWith(200, REMIX_DETECTION, { "Server-Timing": DETECTION_TIMING }));
    await upload(page);

    await expect(page.getByRole("heading", { name: "Posible uso no autorizado: 2 fragmentos de 2 canciones" })).toBeVisible();
    const spans = page.getByRole("group", { name: /Tramos que coinciden/ }).getByRole("button");
    await expect(spans).toHaveCount(2);
    await expect(spans.first()).toHaveAccessibleName(/^A: Melodía 2 de Muestra\. posible copia, 99/);
    await expect(spans.first()).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Original: Melodía 2")).toBeVisible();

    await spans.first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(spans.nth(1)).toBeFocused();
    await expect(spans.nth(1)).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Original: Melodía 3")).toBeVisible();
    await expect(page.getByText("0:05–0:17,5 de 0:30")).toBeVisible();

    await spans.nth(1).click();
    await expect(spans.nth(1)).toHaveAttribute("data-playing", "true");
    await expect.poll(() => page.locator('audio[data-group="ab"]').first().evaluate((audio: HTMLAudioElement) => audio.currentTime)).toBeGreaterThan(15);
    await page.getByRole("button", { name: /^Escuchar el original Melodía 3/ }).first().click();
    await expect(page.getByRole("button", { name: /^Pausar el original Melodía 3/ }).first()).toBeVisible();

    await page.getByText("Cuánto tardó").click();
    await expect(page.getByRole("rowheader", { name: "Buscar en el gestor" })).toBeVisible();
    await expect(page.getByRole("rowheader", { name: "Sacar los MFCC" })).toBeVisible();
  });

  test("a file that is not audio is rejected with the api's reason", async ({ page, context }) => {
    await mockApi(
      context,
      respondWith(415, {
        error: "el archivo no es un audio MP3, WAV, FLAC ni OGG (se revisa su contenido, no su nombre)",
        kind: "UnsupportedAudioError",
      }),
    );
    await upload(page);
    await expect(page.getByRole("heading", { name: "Ese archivo no es un audio que podamos leer" })).toBeVisible();
    await expect(page.getByText("El archivo no es un audio MP3, WAV, FLAC ni OGG (se revisa su contenido, no su nombre).")).toBeVisible();
    await page.getByRole("button", { name: "Elegir otro audio" }).click();
    await expect(page.getByRole("heading", { name: "¿Esta canción usa música del catálogo?" })).toBeVisible();
  });

  test("an original audio says so plainly", async ({ page, context }) => {
    await mockApi(context, respondWith(200, { ...REMIX_DETECTION, detected: false, matches: [] }));
    await upload(page);
    await expect(page.getByRole("heading", { name: "No encontramos coincidencias con el catálogo" })).toBeVisible();
    await expect(page.getByRole("group", { name: /Tramos que coinciden/ })).toHaveCount(0);
  });

  test("a catalog song is played and its similar songs are found without itself", async ({ page, context }) => {
    await mockApi(context, respondWith(200, REMIX_DETECTION, { "Server-Timing": DETECTION_TIMING }));
    await page.goto("/");
    const catalog = page.getByRole("list", { name: "Canciones del catálogo" });
    await catalog.getByRole("button", { name: "Escuchar Melodía 2" }).click();
    await expect(catalog.getByRole("button", { name: "Pausar Melodía 2" })).toBeVisible();
    await catalog.getByRole("listitem").filter({ hasText: "Melodía 2" }).getByRole("button", { name: "Buscar parecidas" }).click();
    await expect(page.getByRole("heading", { name: "Parecidas a «Melodía 2» de Muestra" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Posible uso no autorizado: 1 fragmento de 1 canción" })).toBeVisible();
    await page.getByRole("button", { name: "Adelantar 5 s" }).click();
    await expect(page.getByRole("slider", { name: "Posición en el audio elegido" })).toHaveValue("5");
  });

  test("a demo clip is listened to and analysed with one click", async ({ page, context }) => {
    await mockApi(context, respondWith(200, REMIX_DETECTION));
    await page.goto("/");
    const clips = page.getByRole("list", { name: "Audios de prueba" });
    await clips.getByRole("button", { name: "Escuchar Mezcla de dos canciones" }).click();
    await expect(clips.getByRole("button", { name: "Pausar Mezcla de dos canciones" })).toBeVisible();
    await clips.getByRole("listitem").filter({ hasText: "Mezcla de dos canciones" }).getByRole("button", { name: "Analizar" }).click();
    await expect(page.getByRole("heading", { name: "Posible uso no autorizado: 2 fragmentos de 2 canciones" })).toBeVisible();
  });

  test("weak matches stay hidden until asked for", async ({ page, context }) => {
    const weak = { ...REMIX_DETECTION.matches[1]!, song: { id: 9, title: "Melodía 9", artist: "Muestra" }, score: 0, above_threshold: false };
    await mockApi(context, respondWith(200, { ...REMIX_DETECTION, matches: [...REMIX_DETECTION.matches, weak] }));
    await upload(page);
    const spans = page.getByRole("group", { name: /Tramos que coinciden/ }).getByRole("button");
    await expect(spans).toHaveCount(2);
    await page.getByRole("button", { name: "Mostrar coincidencias débiles (1)" }).click();
    await expect(spans).toHaveCount(3);
  });

  test("a catalog still indexing says so instead of an engine error", async ({ page, context }) => {
    await mockApi(
      context,
      respondWith(400, { error: "La tabla 'cr_segmentos' no tiene un índice HNSW sobre 'ruta'", kind: "DomainError" }),
      { ...READY_CATALOG, segments_indexed: false },
    );
    await page.goto("/");
    await expect(page.getByText("El catálogo se está cargando")).toBeVisible();
    await page.getByLabel("Audio para analizar").setInputFiles(remixFile());
    await expect(page.getByRole("heading", { name: "El catálogo se está cargando" })).toBeVisible();
    await expect(page.getByText(/cr_segmentos/)).toHaveCount(0);
  });

  test("an upload can be cancelled", async ({ page, context }) => {
    await mockApi(context, () => new Promise<void>(() => undefined));
    await upload(page);
    await page.getByRole("button", { name: "Cancelar" }).click();
    await expect(page.getByText("Cancelaste el análisis de «remix de la muestra.wav».")).toBeVisible();
  });
});

test("with the service down, the studio says why and waits", async ({ page, context }) => {
  await context.route("**/api/health", (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await expect(page.getByText("Sin servicio")).toBeVisible();
  await expect(page.getByText(/El servicio de detección no responde/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Elegir un audio" })).toBeDisabled();
});

test.describe("before the app loads", () => {
  test.use({ serviceWorkers: "block" });

  test("the remembered theme is already applied", async ({ page, context }) => {
    await context.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", version: "0.1.0" } }));
    await page.goto("/");
    await page.getByText("Oscuro", { exact: true }).click();
    await context.route("**/assets/index-*.js", () => undefined);
    await page.reload({ waitUntil: "commit" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#root")).toBeEmpty();
  });
});

test("offline, the shell still opens but no api answer is cached", async ({ page, context }) => {
  await context.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", version: "0.1.0" } }));
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
