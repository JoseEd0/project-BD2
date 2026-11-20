import { expect, test } from "@playwright/test";

test("a shortcut runs against the engine and its rows come back", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Índices" }).click();
  await page.getByRole("button", { name: "Índice hash" }).click();
  await page.getByRole("button", { name: /^Ejecutar/ }).click();

  const rows = page.locator("table.grid tbody tr");
  await expect(rows.first()).toContainText("Cusco");
  await expect(page.locator("table.grid thead")).toContainText("ciudad");
  expect(await rows.count()).toBeGreaterThan(1);
});
