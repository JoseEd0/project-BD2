import { defineConfig, devices } from "@playwright/test";

/** El puerto que `vite preview` usa por omisión; `WEB_E2E_PORT` lo cambia. */
const VITE_PREVIEW_DEFAULT_PORT = "4173";
const port = process.env.WEB_E2E_PORT ?? VITE_PREVIEW_DEFAULT_PORT;
const baseURL = `http://localhost:${port}`;

/**
 * Corre contra el build (`npm run build` antes): el service worker solo existe ahí. Sin
 * `WEB_DEV_API_URL`, `vite preview` no reenvía nada, y cada test decide qué responde el API
 * con `context.route`.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run preview -- --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
});
