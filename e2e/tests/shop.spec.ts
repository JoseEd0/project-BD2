import { expect, test } from "@playwright/test";

// Del dataset diminuto (`apps/shop/tests/tiny_dataset.py`): USER-C compró la guitarra y las
// cuerdas en entrenamiento, y se le recomiendan los tres productos que no tiene. Al comprar
// las baquetas, salen de sus recomendaciones, que se recalculan en el gestor sin la caché.
const CUSTOMER = "USER-C";
const PRODUCT = "Drum Sticks 5A";
const STILL_RECOMMENDED = /Instrument Cable 10 ft/;

test("search, open a product, buy it as a customer and see the recommendations recomputed", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Cliente").selectOption(CUSTOMER);
  await page.getByRole("searchbox").fill("drum sticks");
  await page.getByRole("searchbox").press("Enter");
  await page.getByRole("link", { name: new RegExp(PRODUCT) }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: PRODUCT })).toBeVisible();

  const forCustomer = page.waitForResponse(
    (response) => response.url().includes(`/api/usuarios/${CUSTOMER}/recomendaciones`) && response.ok(),
  );
  await page.getByRole("button", { name: "Comprar" }).click();
  await expect(page.getByText(/Compra n\.º \S+ registrada\./)).toBeVisible();
  expect((await forCustomer).headers()["x-cache"]).toBe("miss");

  await expect(page.getByRole("status").filter({ hasText: /^Tus recomendaciones/ })).toContainText("la caché se invalidó");
  const after = page.getByRole("region", { name: "Recomendado para ti, después de la compra" });
  await expect(after.getByRole("link", { name: STILL_RECOMMENDED })).toBeVisible();
  await expect(after.getByRole("link", { name: new RegExp(PRODUCT) })).toHaveCount(0);

  await page.getByRole("link", { name: "Para ti" }).click();
  await expect(page.getByRole("region", { name: "Lo que compraste" }).getByRole("link", { name: new RegExp(PRODUCT) })).toBeVisible();
});
