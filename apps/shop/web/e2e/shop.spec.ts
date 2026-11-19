import { expect, test } from "@playwright/test";

import { AMP, CUSTOMER, fakeShop, GUITAR } from "./fakeShop";

test("search, open a product, buy it and see the recommendations change", async ({ page, context }) => {
  const shop = await fakeShop(context);
  await page.goto("/");
  await page.getByLabel("Cliente").selectOption(CUSTOMER);
  await page.getByRole("searchbox").fill("electric guitar");
  await page.getByRole("searchbox").press("Enter");
  await page.getByRole("link", { name: /Electric Guitar with Maple Neck/ }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Electric Guitar with Maple Neck" })).toBeVisible();
  const bought = page.getByRole("region", { name: /Comprado junto con este producto/ });
  await expect(bought.getByText("comprado junto con este producto por 12 clientes")).toBeVisible();
  await bought.getByText("Ver SQL").click();
  await expect(bought.getByText(/FROM shop_compras_conjuntas/)).toBeVisible();
  await expect(page.getByText("caché: acierto")).toBeVisible();
  await expect(page.getByRole("button", { name: /Centro/ })).toContainText("2 unidades");

  await page.getByRole("button", { name: "Comprar" }).click();
  await expect(page.getByText(/Compra n\.º 511,837 registrada/)).toBeVisible();
  expect(shop.purchases).toEqual([GUITAR]);
  await expect(page.getByText(/Tus recomendaciones cambiaron: 1 producto nuevo; la caché se invalidó/)).toBeVisible();
  const after = page.getByRole("region", { name: "Recomendado para ti, después de la compra" });
  const amplifier = after.getByRole("link", { name: /Practice Amplifier/ });
  await expect(amplifier.getByText("Nuevo", { exact: true })).toBeVisible();
  await expect(after.getByText("Nuevo", { exact: true })).toHaveCount(1);

  await page.getByRole("link", { name: "Para ti" }).click();
  const purchases = page.getByRole("region", { name: "Lo que compraste" });
  await expect(purchases.getByRole("link", { name: /Electric Guitar/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Lo que más va contigo" }).getByRole("link", { name: new RegExp(AMP.slice(-5)) })).toHaveCount(0);
});

test("buying asks for a customer first", async ({ page, context }) => {
  await fakeShop(context);
  await page.goto(`/producto/${GUITAR}`);
  await page.getByRole("button", { name: "Elegir cliente" }).click();
  await expect(page.getByLabel("Cliente")).toBeFocused();
});

test("a denied location keeps Lima and says how to choose another point", async ({ page, context }) => {
  await fakeShop(context);
  await context.clearPermissions();
  await page.addInitScript(() => {
    navigator.geolocation.getCurrentPosition = (_success, failure) =>
      failure?.({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: "denied" } as GeolocationPositionError);
  });
  await page.goto(`/producto/${GUITAR}`);
  await page.getByRole("button", { name: "Usar mi ubicación" }).click();
  await expect(page.getByText(/No diste permiso de ubicación: seguimos desde el centro de Lima/)).toBeVisible();
});

test("a photo the service cannot read says so", async ({ page, context }) => {
  await fakeShop(context);
  await context.route("**/api/busqueda/foto**", (route) =>
    route.fulfill({ status: 400, json: { error: "no se pudo leer", kind: "UnreadablePhotoError" } }),
  );
  await page.goto("/");
  await page.getByRole("radio", { name: "Foto" }).check();
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "rojo.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==",
      "base64",
    ),
  });
  await expect(page.getByText("No pudimos leer esa foto")).toBeVisible();
});

test("at 390 px nothing scrolls sideways", async ({ page, context }) => {
  await fakeShop(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/producto/${GUITAR}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: /Comprado junto con este producto/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBe(0);
});
