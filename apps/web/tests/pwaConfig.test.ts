import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { MANIFEST_ICONS, pwaOptions, SHELL_GLOB_PATTERNS } from "../pwa.config";
import { SERVICE_PATH_PREFIXES } from "../src/services/catalog";
import { servicePathPattern, serviceUrlPattern } from "../src/services/serviceRequests";

const PUBLIC_DIRECTORY = join(import.meta.dirname, "..", "public");
// Un PNG guarda ancho y alto como enteros de 32 bits tras la firma y la cabecera IHDR.
const PNG_WIDTH_OFFSET = 16;
const PNG_HEIGHT_OFFSET = 20;

describe("service request patterns", () => {
  const urls = serviceUrlPattern(SERVICE_PATH_PREFIXES);
  const paths = servicePathPattern(SERVICE_PATH_PREFIXES);

  it("match the engine and every app, on this origin or the gateway's", () => {
    for (const url of [
      "http://localhost:5173/api/health",
      "https://gateway.test/api",
      "https://gateway.test/apps/shop/health?x=1",
      "https://gateway.test/api?session=1",
    ]) {
      expect(urls.test(url)).toBe(true);
    }
    for (const path of ["/api", "/api/query", "/apps/faces/health", "/apps?x"]) {
      expect(paths.test(path)).toBe(true);
    }
  });

  it("leave the portal's own pages and assets alone", () => {
    for (const url of ["https://gateway.test/", "https://gateway.test/tienda", "https://gateway.test/apiary", "https://gateway.test/assets/api.js"]) {
      expect(urls.test(url)).toBe(false);
    }
    for (const path of ["/", "/copyright", "/apiary", "/applications"]) {
      expect(paths.test(path)).toBe(false);
    }
  });
});

describe("workbox configuration", () => {
  const workbox = pwaOptions.workbox;

  it("precaches only built shell files", () => {
    expect(workbox?.globPatterns).toEqual(SHELL_GLOB_PATTERNS);
    expect(SHELL_GLOB_PATTERNS.join()).not.toMatch(/json|api/);
  });

  it("sends every service request to the network and never caches it", () => {
    const routes = workbox?.runtimeCaching ?? [];
    expect(routes.map((route) => route.handler)).toEqual(["NetworkOnly"]);
    const [route] = routes;
    expect(String(route?.urlPattern)).toBe(String(serviceUrlPattern(SERVICE_PATH_PREFIXES)));
  });

  it("never answers a service path with the cached index.html", () => {
    expect(workbox?.navigateFallback).toBe("index.html");
    expect(workbox?.navigateFallbackDenylist?.map(String)).toEqual([String(servicePathPattern(SERVICE_PATH_PREFIXES))]);
  });
});

describe("manifest", () => {
  const manifest = pwaOptions.manifest;

  it("has what Chrome asks to install the app", () => {
    expect(manifest).toMatchObject({ display: "standalone", start_url: "/", scope: "/", id: "/" });
    expect(manifest && manifest.name).toBeTruthy();
    expect(manifest && manifest.short_name).toBeTruthy();
    const sizes = MANIFEST_ICONS.map((icon) => `${icon.sizes} ${icon.purpose}`);
    expect(sizes).toEqual(expect.arrayContaining(["192x192 any", "512x512 any", "512x512 maskable"]));
  });

  it.each(MANIFEST_ICONS)("ships $src at its declared size", async (icon) => {
    const png = await readFile(join(PUBLIC_DIRECTORY, icon.src));
    const size = `${png.readUInt32BE(PNG_WIDTH_OFFSET)}x${png.readUInt32BE(PNG_HEIGHT_OFFSET)}`;
    expect(size).toBe(icon.sizes);
  });
});
