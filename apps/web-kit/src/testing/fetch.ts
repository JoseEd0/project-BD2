import { vi } from "vitest";

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

/** Sustituye `fetch` por uno que responde según la ruta pedida. */
export function stubFetch(respond: (url: URL) => Response | Promise<Response>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: Request) => Promise.resolve(respond(new URL(input.url)))),
  );
}
