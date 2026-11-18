import { describe, expect, it } from "vitest";

import { appById, ENGINE, PORTAL_APPS, SERVICE_PATH_PREFIXES, serviceBaseUrl } from "../src/services/catalog";

describe("catalog", () => {
  it("has four apps with distinct ids, slugs and mount paths", () => {
    expect(PORTAL_APPS).toHaveLength(4);
    for (const key of ["id", "slug", "mountPath"] as const) {
      expect(new Set(PORTAL_APPS.map((app) => app[key])).size).toBe(PORTAL_APPS.length);
    }
  });

  it("mounts every service under a prefix the service worker leaves to the network", () => {
    for (const service of [ENGINE, ...PORTAL_APPS]) {
      expect(SERVICE_PATH_PREFIXES.some((prefix) => service.mountPath.startsWith(prefix))).toBe(true);
    }
  });

  it("joins the base url and the mount path without doubling slashes", () => {
    expect(serviceBaseUrl("", ENGINE)).toBe("/api");
    expect(serviceBaseUrl("https://gateway.test/", ENGINE)).toBe("https://gateway.test/api");
    expect(serviceBaseUrl("https://gateway.test//", appById("shop"))).toBe("https://gateway.test/apps/shop");
  });

  it("finds an app by id and refuses an unknown one", () => {
    expect(appById("faces").slug).toBe("rostros");
    expect(() => appById("nope" as never)).toThrow("nope");
  });
});
