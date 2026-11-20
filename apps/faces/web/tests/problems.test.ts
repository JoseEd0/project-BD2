import { describe, expect, it } from "vitest";

import { networkProblem, problemFromResponse } from "../src/api/problems";
import { browserOf, cameraProblemOf, cameraSupportProblem, permissionSteps, secureAddressFor } from "../src/camera/problems";
import { streamUrl } from "../src/config";

describe("api problems", () => {
  it("keeps the service's kind and message", () => {
    expect(problemFromResponse(409, { error: "Ya estás registrado como «Ana».", kind: "AlreadyEnrolledError", request_id: "r" })).toEqual({
      kind: "AlreadyEnrolledError",
      message: "Ya estás registrado como «Ana».",
      status: 409,
    });
  });

  it("explains what the proxy answered when the service did not", () => {
    expect(problemFromResponse(429, "<html>")).toMatchObject({ kind: "Http429", message: expect.stringContaining("espera") });
    expect(problemFromResponse(413, "<html>").message).toContain("más liviana");
    expect(problemFromResponse(502, null)).toMatchObject({ kind: "Http502", message: expect.stringContaining("no responde") });
    expect(problemFromResponse(418, {}).message).toContain("Inténtalo");
  });

  it("names a network failure", () => {
    expect(networkProblem()).toMatchObject({ kind: "NetworkError", status: null });
  });
});

describe("camera problems", () => {
  it("maps the browser's errors", () => {
    expect(cameraProblemOf(new DOMException("x", "NotAllowedError"))).toBe("denied");
    expect(cameraProblemOf(new DOMException("x", "NotFoundError"))).toBe("not-found");
    expect(cameraProblemOf(new DOMException("x", "NotReadableError"))).toBe("in-use");
    expect(cameraProblemOf(new Error("x"))).toBe("unknown");
    expect(cameraProblemOf(null)).toBe("unknown");
  });

  it("knows before asking that there is no camera without https", () => {
    expect(cameraSupportProblem({ isSecureContext: false, mediaDevices: undefined })).toBe("insecure");
    expect(cameraSupportProblem({ isSecureContext: true, mediaDevices: undefined })).toBe("unsupported");
    expect(cameraSupportProblem({ isSecureContext: true, mediaDevices: { getUserMedia: () => undefined } as unknown as MediaDevices })).toBeNull();
  });

  it("tells each browser how to allow the camera again", () => {
    const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
    const android = "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36";
    const samsung = "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0 Mobile Safari/537.36";
    expect(browserOf(iphone)).toBe("ios");
    expect(browserOf(android)).toBe("android-chrome");
    expect(browserOf(samsung)).toBe("samsung");
    expect(permissionSteps("ios")[0]).toContain("Ajustes");
    expect(permissionSteps("android-chrome").join(" ")).toContain("Permitir");
  });

  it("offers the https address next to the compose's http port", () => {
    const pairs = { "8104": "8404" };
    expect(secureAddressFor({ protocol: "http:", hostname: "192.168.1.20", port: "8104", pathname: "/registro" }, pairs)).toBe(
      "https://192.168.1.20:8404/registro",
    );
    expect(secureAddressFor({ protocol: "http:", hostname: "x", port: "9000", pathname: "/" }, pairs)).toBeNull();
    expect(secureAddressFor({ protocol: "https:", hostname: "x", port: "8404", pathname: "/" }, pairs)).toBeNull();
  });
});

describe("socket address", () => {
  it("uses wss on an https page and the same api prefix", () => {
    expect(streamUrl("/api", { protocol: "https:", host: "rostros.test:8404" })).toBe("wss://rostros.test:8404/api/ws/recognize");
    expect(streamUrl("/api/", { protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/api/ws/recognize");
    expect(streamUrl("https://faces.test/api", { protocol: "http:", host: "x" })).toBe("wss://faces.test/api/ws/recognize");
  });
});
