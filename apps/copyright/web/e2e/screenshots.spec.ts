import { test } from "@playwright/test";

import { DETECTION_TIMING, mockApi, remixFile, REMIX_DETECTION, respondWith } from "./fixtures";

/**
 * Capturas para revisar el diseño y adjuntarlas a un PR, no un test: corre solo con
 * `WEB_SCREENSHOTS_DIR=<carpeta> npm run e2e -- screenshots`.
 */
const directory = process.env.WEB_SCREENSHOTS_DIR;
const VIEWPORTS = { desktop: { width: 1440, height: 1100 }, mobile: { width: 390, height: 844 } } as const;
const THEMES = ["light", "dark"] as const;

test.skip(directory === undefined, "sin WEB_SCREENSHOTS_DIR no hay capturas");
test.use({ serviceWorkers: "block" });

for (const [device, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`${device} ${theme}`, async ({ page, context }) => {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await mockApi(context, respondWith(200, REMIX_DETECTION, { "Server-Timing": DETECTION_TIMING }));
      await page.goto("/");
      await page.getByRole("heading", { level: 1 }).waitFor();
      await page.screenshot({ path: `${directory}/${device}-${theme}-empty.png`, fullPage: true });
      await page.getByLabel("Audio para analizar").setInputFiles(remixFile());
      await page.getByRole("heading", { name: /Posible uso no autorizado/ }).waitFor();
      await page.getByText("Cuánto tardó").click();
      await page.locator(".waveform").waitFor();
      await page.screenshot({ path: `${directory}/${device}-${theme}-result.png`, fullPage: true });
    });
  }
}
