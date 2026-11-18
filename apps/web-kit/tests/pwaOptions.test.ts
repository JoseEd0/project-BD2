import { describe, expect, it } from "vitest";

import { apiPathPattern, apiUrlPattern, createPwaOptions, SHELL_GLOB_PATTERNS } from "../src/pwa/index.ts";

const MANIFEST = { name: "Tienda", short_name: "Tienda", display: "standalone" } as const;

describe("api request patterns", () => {
  const urls = apiUrlPattern(["/api"]);
  const paths = apiPathPattern(["/api"]);

  it("match the api on this origin or another one", () => {
    for (const url of [
      "http://localhost:5173/api/health",
      "https://shop.test/api",
      "https://shop.test/api?session=1",
      "https://api.shop.test/api/products/1",
    ]) {
      expect(urls.test(url)).toBe(true);
    }
    for (const path of ["/api", "/api/query", "/api?x"]) {
      expect(paths.test(path)).toBe(true);
    }
  });

  it("leave the app's own pages and assets alone", () => {
    for (const url of ["https://shop.test/", "https://shop.test/carrito", "https://shop.test/apiary", "https://shop.test/assets/api.js"]) {
      expect(urls.test(url)).toBe(false);
    }
    for (const path of ["/", "/carrito", "/apiary", "/applications"]) {
      expect(paths.test(path)).toBe(false);
    }
  });

  it("escape a prefix so its characters are literal", () => {
    const dotted = apiPathPattern(["/v1.0"]);
    expect(dotted.test("/v1.0/x")).toBe(true);
    expect(dotted.test("/v1x0/x")).toBe(false);
  });
});

describe("createPwaOptions", () => {
  it("keeps the app's manifest and lets it register the worker on its own terms", () => {
    const options = createPwaOptions({ manifest: MANIFEST });
    expect(options.manifest).toBe(MANIFEST);
    expect(options).toMatchObject({ registerType: "prompt", injectRegister: false });
  });

  it("precaches only built shell files", () => {
    const { workbox } = createPwaOptions({ manifest: MANIFEST });
    expect(workbox?.globPatterns).toEqual(SHELL_GLOB_PATTERNS);
    expect(SHELL_GLOB_PATTERNS.join()).not.toMatch(/json|api/);
  });

  it.each([[undefined, ["/api"]], [["/api", "/ws"], ["/api", "/ws"]]])(
    "sends every request under %j to the network and never caches it",
    (apiPrefixes, expected) => {
      const { workbox } = createPwaOptions({ manifest: MANIFEST, ...(apiPrefixes && { apiPrefixes }) });
      const routes = workbox?.runtimeCaching ?? [];
      expect(routes.map((route) => route.handler)).toEqual(["NetworkOnly"]);
      expect(String(routes[0]?.urlPattern)).toBe(String(apiUrlPattern(expected)));
    },
  );

  it("never answers an api path with the cached index.html", () => {
    const { workbox } = createPwaOptions({ manifest: MANIFEST, apiPrefixes: ["/backend"] });
    expect(workbox?.navigateFallback).toBe("index.html");
    expect(workbox?.navigateFallbackDenylist?.map(String)).toEqual([String(apiPathPattern(["/backend"]))]);
  });
});
