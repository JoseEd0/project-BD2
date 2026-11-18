import { describe, expect, it } from "vitest";

import { createHealthClient } from "../src/api/clients";
import { healthFromFailure, healthFromResponse } from "../src/services/health";
import { probeHealth } from "../src/services/probeHealth";
import { jsonResponse } from "./support/fetch";

const BASE_URL = "http://portal.test/api";
const TIMEOUT_MS = 50;

function clientAnswering(respond: (request: Request) => Promise<Response>) {
  return createHealthClient({ baseUrl: BASE_URL, fetch: respond });
}

describe("healthFromResponse", () => {
  it("is available on a 2xx with status ok, keeping version and timings", () => {
    expect(healthFromResponse(200, { status: "ok", version: "0.1.0" }, "total;dur=1")).toEqual({
      state: "available",
      version: "0.1.0",
      serverTiming: "total;dur=1",
    });
    expect(healthFromResponse(204, { status: "ok" }, null)).toMatchObject({ state: "available", version: null });
  });

  it("is an http error outside 2xx, with the code", () => {
    expect(healthFromResponse(503, { status: "ok" }, null)).toEqual({
      state: "unavailable",
      reason: "http-error",
      httpStatus: 503,
    });
  });

  it("is an unexpected body when someone else answered", () => {
    for (const body of [null, "ok", [], { status: "starting" }, { healthy: true }]) {
      expect(healthFromResponse(200, body, null)).toMatchObject({ reason: "unexpected-body" });
    }
  });
});

describe("healthFromFailure", () => {
  it("tells a timeout, a non-JSON body and a network failure apart", () => {
    expect(healthFromFailure(new DOMException("slow", "TimeoutError"))).toMatchObject({ reason: "timeout" });
    expect(healthFromFailure(new SyntaxError("Unexpected token <"))).toMatchObject({ reason: "unexpected-body" });
    expect(healthFromFailure(new TypeError("Failed to fetch"))).toMatchObject({ reason: "unreachable" });
    expect(healthFromFailure("anything")).toMatchObject({ reason: "unreachable" });
  });
});

describe("probeHealth", () => {
  it("asks /health under the service base and reads Server-Timing", async () => {
    const requested: string[] = [];
    const client = clientAnswering((request) => {
      requested.push(request.url);
      return Promise.resolve(jsonResponse({ status: "ok" }, { headers: { "Server-Timing": "total;dur=0.3" } }));
    });
    await expect(probeHealth(client, new AbortController().signal, TIMEOUT_MS)).resolves.toEqual({
      state: "available",
      version: null,
      serverTiming: "total;dur=0.3",
    });
    expect(requested).toEqual([`${BASE_URL}/health`]);
  });

  it("maps an HTML page served in place of the service to unavailable", async () => {
    const client = clientAnswering(() => Promise.resolve(new Response("<!doctype html>", { status: 200 })));
    await expect(probeHealth(client, new AbortController().signal, TIMEOUT_MS)).resolves.toMatchObject({
      reason: "unexpected-body",
    });
  });

  it("maps a gateway error and a refused connection to unavailable", async () => {
    const failing = clientAnswering(() => Promise.resolve(new Response("Bad Gateway", { status: 502 })));
    await expect(probeHealth(failing, new AbortController().signal, TIMEOUT_MS)).resolves.toMatchObject({
      reason: "http-error",
      httpStatus: 502,
    });
    const refused = clientAnswering(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(probeHealth(refused, new AbortController().signal, TIMEOUT_MS)).resolves.toMatchObject({
      reason: "unreachable",
    });
  });

  it("gives up after the timeout", async () => {
    const hanging = clientAnswering(
      (request) =>
        new Promise((_, reject) => request.signal.addEventListener("abort", () => reject(request.signal.reason))),
    );
    await expect(probeHealth(hanging, new AbortController().signal, TIMEOUT_MS)).resolves.toMatchObject({
      reason: "timeout",
    });
  });

  it("rethrows when the caller cancels, so the query is dropped instead of marked down", async () => {
    const cancellation = new AbortController();
    const hanging = clientAnswering(
      (request) =>
        new Promise((_, reject) => request.signal.addEventListener("abort", () => reject(request.signal.reason))),
    );
    const probe = probeHealth(hanging, cancellation.signal, TIMEOUT_MS * 100);
    cancellation.abort();
    await expect(probe).rejects.toBeDefined();
  });
});
