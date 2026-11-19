import { expect, test, type Page } from "@playwright/test";

import { PERSON_ID, centeredFace, fakeService, fakeSocket } from "./fakeService";

// Sin service worker, para que las rutas simuladas vean cada petición de la página.
test.use({ serviceWorkers: "block" });

async function acceptConsentAndName(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("link", { name: /Registrarme/ }).click();
  await expect(page.getByText("Reconocerte en esta demostración", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Aceptar y continuar" })).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Aceptar y continuar" }).click();
  await page.getByLabel("Tu nombre").fill(name);
  await page.getByRole("button", { name: "Abrir la cámara" }).click();
}

test("enroll with three automatic shots, be recognized and delete the data", async ({ page, context }) => {
  const service = await fakeService(context, { shots: [{ reject: "blurry", message: "La foto salió borrosa. Quédate quieto." }] });
  const guide = await fakeSocket(page, { faces: centeredFace("unknown") });
  await acceptConsentAndName(page, "Ana");

  await expect(page.getByRole("heading", { name: "Registro de Ana" })).toBeVisible();
  await expect(page.getByText("La foto salió borrosa. Quédate quieto.")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Listo, Ana" })).toBeVisible({ timeout: 30_000 });
  expect(service.shots).toBe(4);
  expect(guide.frames).toBeGreaterThan(0);

  const code = page.getByTestId("person-code");
  await expect(code).toHaveText("0123 4567 89ab cdef 0123 4567 89ab cdef");
  await expect(page.getByText(/Guárdalo/)).toBeVisible();
  const stored = await page.evaluate(() => window.localStorage.getItem("rostros.personas"));
  expect(stored).toContain(PERSON_ID);

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await fakeSocket(page, { faces: centeredFace("known", "Ana", 0.71) });
  await page.getByRole("link", { name: "Probar ahora" }).click();
  await expect(page.getByLabel(/^Ana, parecido 71/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/En vivo · \d+ ms/)).toBeVisible();
  await expect(page.getByText(/Servidor 20 ms/)).toBeVisible();

  await page.getByRole("link", { name: "Volver al inicio" }).click();
  await page.getByRole("link", { name: /Mis datos/ }).click();
  await expect(page.getByText(/3 huellas numéricas/)).toBeVisible();
  await page.getByRole("button", { name: /Borrar mis datos/ }).click();
  await page.getByRole("button", { name: "Sí, borrar" }).click();
  await expect(page.getByText(/Listo: borramos a Ana/)).toBeVisible();
  expect(service.deleted).toEqual([PERSON_ID]);
  expect(await page.evaluate(() => window.localStorage.getItem("rostros.personas"))).toBe("[]");
});

test("a duplicate is told clearly and nothing is stored", async ({ page, context }) => {
  await fakeService(context, {
    shots: [{ status: 409, body: { error: "Ya estás registrado como «Ana».", kind: "AlreadyEnrolledError", request_id: "r" } }],
  });
  await fakeSocket(page, { faces: centeredFace("unknown") });
  await acceptConsentAndName(page, "Ana");
  await expect(page.getByRole("heading", { name: "Ya estás registrado" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Ya estás registrado como «Ana».")).toBeVisible();
  expect(await page.evaluate(() => window.localStorage.getItem("rostros.personas"))).toBeNull();
});

test("the guide asks to come closer before spending a shot", async ({ page, context }) => {
  const service = await fakeService(context);
  await fakeSocket(page, {
    faces: ({ width, height }) => [
      { track: 1, box: { x: width / 2 - 20, y: height / 2 - 24, width: 40, height: 48 }, status: "too_small", label: "acércate", display_name: null, score: null, cached: false },
    ],
  });
  await acceptConsentAndName(page, "Ana");
  await expect(page.getByText("Acércate un poco.")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1_000);
  expect(service.shots).toBe(0);
});

test("a full replica is retried with a wait and the session resumes", async ({ page, context }) => {
  await fakeService(context);
  const socket = await fakeSocket(page, { faces: centeredFace("unknown"), closeFirstWith: 1013 });
  await page.goto("/reconocer");
  await expect(page.getByText("Hay mucha gente usando el reconocimiento")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Servicio lleno/)).toBeVisible();
  await expect(page.getByLabel("persona desconocida")).toBeVisible({ timeout: 15_000 });
  expect(socket.connections).toBe(2);
});

test("an idle close waits for a touch and resumes with the same session", async ({ page, context }) => {
  await fakeService(context);
  const socket = await fakeSocket(page, { faces: centeredFace("unknown") });
  await page.goto("/reconocer");
  await expect(page.getByLabel("persona desconocida")).toBeVisible({ timeout: 15_000 });
  await socket.sockets[0]?.close({ code: 4408, reason: "" });
  await expect(page.getByText("En pausa · toca para seguir")).toBeVisible();
  await page.getByText("En pausa · toca para seguir").click();
  await expect(page.getByText(/En vivo/)).toBeVisible({ timeout: 10_000 });
  expect(socket.connections).toBe(2);
  expect(socket.hellos).toContainEqual({ type: "hello", session: "5".repeat(32), track_cache: true });
});

test("a hidden tab stops the camera and the stream, and they come back", async ({ page, context }) => {
  await fakeService(context);
  await fakeSocket(page, { faces: centeredFace("unknown") });
  await page.goto("/reconocer");
  await expect(page.getByLabel("persona desconocida")).toBeVisible({ timeout: 15_000 });
  const setVisibility = (state: "hidden" | "visible") =>
    page.evaluate((value) => {
      Object.defineProperty(document, "visibilityState", { value, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    }, state);
  await setVisibility("hidden");
  await expect.poll(() => page.evaluate(() => document.querySelector("video")?.srcObject === null)).toBe(true);
  await setVisibility("visible");
  await expect.poll(() => page.evaluate(() => document.querySelector("video")?.srcObject instanceof MediaStream)).toBe(true);
  await expect(page.getByText(/En vivo/)).toBeVisible({ timeout: 10_000 });
});

test("a denied camera explains how to allow it again", async ({ page, context }) => {
  await fakeService(context);
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("denied", "NotAllowedError"));
  });
  await page.goto("/reconocer");
  await expect(page.getByRole("heading", { name: "Permite el uso de la cámara" })).toBeVisible();
  await expect(page.getByText(/Permisos › Cámara › Permitir/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Intentar de nuevo" })).toBeVisible();
});

test("without https it says how to open the app", async ({ page, context }) => {
  await fakeService(context);
  await page.addInitScript(() => {
    Object.defineProperty(window, "isSecureContext", { value: false });
  });
  await page.goto("/reconocer");
  await expect(page.getByRole("heading", { name: "Abre la app por HTTPS" })).toBeVisible();
  await expect(page.getByText(/trycloudflare\.com/)).toBeVisible();
});

test("a phone without a camera is told so", async ({ page, context }) => {
  await fakeService(context);
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("none", "NotFoundError"));
  });
  await page.goto("/registro");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Aceptar y continuar" }).click();
  await page.getByLabel("Tu nombre").fill("Ana");
  await page.getByRole("button", { name: "Abrir la cámara" }).click();
  await expect(page.getByRole("heading", { name: "No encontramos una cámara" })).toBeVisible();
});

test("the service down is shown on the home screen", async ({ page, context }) => {
  await context.route("**/api/health", (route) => route.fulfill({ status: 502, body: "Bad Gateway" }));
  await page.goto("/");
  await expect(page.getByText("Servicio no disponible.")).toBeVisible();
});
