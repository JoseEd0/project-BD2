import { describe, expect, it } from "vitest";

import { createServiceClient, REQUEST_ID_HEADER, serviceBaseUrl } from "../src/index.ts";
import { jsonResponse } from "../src/testing/index.ts";

interface EchoPaths {
  "/items/{id}": {
    get: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": { id: string } } } };
    };
  };
}

function recordingClient(requests: Request[]) {
  return createServiceClient<EchoPaths>({
    baseUrl: "http://app.test/api",
    fetch: (request) => {
      requests.push(request);
      return Promise.resolve(jsonResponse({ id: "7" }));
    },
  });
}

describe("serviceBaseUrl", () => {
  it("joins the origin and the api prefix without doubling slashes", () => {
    expect(serviceBaseUrl("")).toBe("/api");
    expect(serviceBaseUrl("https://shop.test/")).toBe("https://shop.test/api");
    expect(serviceBaseUrl("https://shop.test//", "/v1")).toBe("https://shop.test/v1");
  });
});

describe("createServiceClient", () => {
  it("sends typed requests under the base url", async () => {
    const requests: Request[] = [];
    const { data } = await recordingClient(requests).GET("/items/{id}", { params: { path: { id: "7" } } });
    expect(data).toEqual({ id: "7" });
    expect(requests.map((request) => request.url)).toEqual(["http://app.test/api/items/7"]);
  });

  it("stamps every request with its own request id", async () => {
    const requests: Request[] = [];
    const client = recordingClient(requests);
    await client.GET("/items/{id}", { params: { path: { id: "1" } } });
    await client.GET("/items/{id}", { params: { path: { id: "2" } } });
    const ids = requests.map((request) => request.headers.get(REQUEST_ID_HEADER));
    expect(ids.every((id) => id !== null && /^[0-9a-f-]{36}$/.test(id))).toBe(true);
    expect(new Set(ids).size).toBe(2);
  });

  it("keeps a request id the caller already chose", async () => {
    const requests: Request[] = [];
    await recordingClient(requests).GET("/items/{id}", {
      params: { path: { id: "1" } },
      headers: { [REQUEST_ID_HEADER]: "retry-42" },
    });
    expect(requests[0]?.headers.get(REQUEST_ID_HEADER)).toBe("retry-42");
  });
});
