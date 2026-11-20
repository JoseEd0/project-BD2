import { fileURLToPath } from "node:url";

import { defineConfig, devices, type Project } from "@playwright/test";

/**
 * Cada app en su propia URL, la del compose. Sin variables, los puertos por omisión del
 * compose (`docker compose up` a secas); `e2e/run.sh` levanta el suyo en los 28xxx y los
 * exporta con los mismos nombres que lee el compose.
 */
function localUrl(portVariable: string, defaultPort: string): string {
  return `http://localhost:${process.env[portVariable] ?? defaultPort}`;
}

const DEFAULT_CAMERA_VIDEO = fileURLToPath(new URL(".state/camera.y4m", import.meta.url));
const cameraVideo = process.env.E2E_CAMERA_VIDEO ?? DEFAULT_CAMERA_VIDEO;

function desktopApp(name: string, baseURL: string): Project {
  return { name, testMatch: `${name}.spec.ts`, use: { ...devices["Desktop Chrome"], baseURL } };
}

/**
 * Contra el sistema real: nada de `context.route`. Los servicios responden de verdad, así que
 * las esperas son más largas que en los e2e de cada interfaz. Sin service worker, para que
 * cada petición llegue al nginx de la app. Los tests escriben en la base (una compra, un
 * registro), así que corren una sola vez, en serie, sobre una base recién sembrada.
 *
 * La cámara de rostros es la simulada de Chromium alimentada con un Y4M de una foto real
 * (`--use-file-for-fake-video-capture`, ver `apps/faces/tests/fake_camera.py`).
 */
export default defineConfig({
  testDir: "tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure", serviceWorkers: "block" },
  projects: [
    desktopApp("console", localUrl("MINIGESTOR_WEB_PORT", "8080")),
    desktopApp("copyright", localUrl("COPYRIGHT_WEB_PORT", "8101")),
    desktopApp("shop", localUrl("SHOP_WEB_PORT", "8102")),
    desktopApp("papers", localUrl("PAPERS_WEB_PORT", "8103")),
    {
      name: "faces",
      testMatch: "faces.spec.ts",
      use: {
        ...devices["Pixel 7"],
        baseURL: localUrl("FACES_WEB_PORT", "8104"),
        permissions: ["camera"],
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            `--use-file-for-fake-video-capture=${cameraVideo}`,
          ],
        },
      },
    },
  ],
});
