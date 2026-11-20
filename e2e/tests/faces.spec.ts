import { expect, test } from "@playwright/test";

// La cámara simulada repite un retrato de Eileen Collins (NASA, dominio público), así que el
// registro es de frente: el compose de la suite pide una sola pose (e2e/compose.yaml).
const NAME = "Eileen";
const PERSON_CODE = /^[0-9a-f]{4}( [0-9a-f]{4}){7}$/;

test("consent, enroll with the camera, be recognized and delete the data", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /Registrarme/ }).click();
  await expect(page.getByRole("button", { name: "Aceptar y continuar" })).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Aceptar y continuar" }).click();
  await page.getByLabel("Tu nombre").fill(NAME);
  await page.getByRole("button", { name: "Abrir la cámara" }).click();

  await expect(page.getByRole("heading", { name: `Registro de ${NAME}` })).toBeVisible();
  await expect(page.getByRole("heading", { name: `Listo, ${NAME}` })).toBeVisible({ timeout: 60_000 });
  const code = await page.getByTestId("person-code").innerText();
  expect(code).toMatch(PERSON_CODE);
  const personId = code.replaceAll(" ", "");

  await page.getByRole("link", { name: "Probar ahora" }).click();
  await expect(page.getByLabel(new RegExp(`^${NAME}, parecido \\d+`))).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/En vivo · \d+ ms/)).toBeVisible();

  await page.getByRole("link", { name: "Volver al inicio" }).click();
  await page.getByRole("link", { name: /Mis datos/ }).click();
  await expect(page.getByText("Tu nombre y 1 huellas numéricas; ninguna foto")).toBeVisible();
  await page.getByRole("button", { name: /Borrar mis datos/ }).click();
  await page.getByRole("button", { name: "Sí, borrar" }).click();
  await expect(page.getByText(new RegExp(`Listo: borramos a ${NAME}`))).toBeVisible();

  const deleted = await page.request.get("/api/person", { headers: { "X-Person-Id": personId } });
  expect(deleted.status()).toBe(404);
});
