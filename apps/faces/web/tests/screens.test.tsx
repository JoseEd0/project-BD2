import { jsonResponse, stubFetch } from "@minigestor/web-kit/testing";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { FaceOverlay, faceCaption } from "../src/components/FaceOverlay";
import { recognitionSummary, timingsSummary } from "../src/pages/RecognizePage";
import type { StreamFace } from "../src/realtime/protocol";
import { routes } from "../src/routes";
import { loadPeople, rememberPerson } from "../src/storage/people";
import { PERSON, PERSON_ID } from "./fixtures";

const CONSENT = {
  version: "1-abcdef123456",
  title: "Consentimiento para registrar tu rostro",
  purpose: "Reconocerte en esta demostración.",
  stored_data: ["El nombre que escribas."],
  not_stored: "Tus fotos.",
  retention_days: 30,
  retention: "Se borra a los 30 días.",
  rights: "Puedes borrar tu registro.",
  text: "…",
};

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function service(extra: (url: URL, request: Request) => Response | null = () => null): Request[] {
  const requests: Request[] = [];
  stubFetch((url) => {
    if (url.pathname === "/api/health") {
      return jsonResponse({ status: "ok", version: "0.1.0" });
    }
    if (url.pathname === "/api/consent") {
      return jsonResponse(CONSENT);
    }
    return new Response(null, { status: 404 });
  });
  const stubbed = globalThis.fetch as unknown as { getMockImplementation(): (request: Request) => Promise<Response> };
  const base = stubbed.getMockImplementation();
  (globalThis.fetch as unknown as { mockImplementation(fn: (request: Request) => Promise<Response>): void }).mockImplementation((request: Request) => {
    requests.push(request);
    return Promise.resolve(extra(new URL(request.url), request) ?? base(request));
  });
  return requests;
}

function face(status: StreamFace["status"], displayName: string | null, score: number | null): StreamFace {
  return { track: 1, box: { x: 100, y: 100, width: 200, height: 240 }, status, label: displayName ?? "desconocido", displayName, score, cached: false };
}

describe("screens", () => {
  it("opens on the three things the app does and the service state", async () => {
    service();
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Tu cara, tres tomas y listo" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Registrarme/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Reconocer en vivo/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /A qué famoso te pareces/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Mis datos/ })).toBeTruthy();
    expect(await screen.findByText("Servicio disponible")).toBeTruthy();
  });

  it("says when the service is down and lets the person ask again", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByText(/no responde/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Volver a comprobar" })).toBeTruthy();
  });

  it("asks for an explicit yes to the served consent before the name", async () => {
    service();
    const user = userEvent.setup();
    renderAt("/registro");
    expect(await screen.findByText("Reconocerte en esta demostración.", undefined, { timeout: 5000 })).toBeTruthy();
    expect(screen.getByText("Versión 1-abcdef123456")).toBeTruthy();
    const accept = screen.getByRole("button", { name: "Aceptar y continuar" });
    expect((accept as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("checkbox"));
    await user.click(accept);
    expect(await screen.findByRole("heading", { name: "¿Cómo te llamas?" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Abrir la cámara" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("deletes the stored person after a confirmation", async () => {
    rememberPerson({ personId: PERSON_ID, displayName: "Ana", expiresAt: PERSON.expires_at });
    const requests = service((url, request) => {
      if (url.pathname !== "/api/person") {
        return null;
      }
      return request.method === "DELETE" ? new Response(null, { status: 204 }) : jsonResponse(PERSON);
    });
    const user = userEvent.setup();
    renderAt("/mis-datos");
    expect(await screen.findByText(/3 huellas numéricas/, undefined, { timeout: 5000 })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Borrar mis datos/ }));
    await user.click(screen.getByRole("button", { name: "Sí, borrar" }));
    expect(await screen.findByText(/Listo: borramos a Ana/)).toBeTruthy();
    const deletion = requests.find((request) => request.method === "DELETE");
    expect(deletion?.headers.get("X-Person-Id")).toBe(PERSON_ID);
    expect(new URL(deletion?.url ?? "").pathname).toBe("/api/person");
    expect(loadPeople()).toEqual([]);
  });

  it("shows the public figures that look like the chosen photo, with their LFW photo", async () => {
    const people = [
      { id: 7, name: "Eileen Collins", similarity: 0.61, percent: 61, photos: 2, photo_path: "/lookalikes/7/photo" },
      { id: 3, name: "Bo Li", similarity: 0.24, percent: 24, photos: 1, photo_path: "/lookalikes/3/photo" },
    ];
    const requests = service((url) => (url.pathname === "/api/lookalikes" ? jsonResponse({ people, message: "Las personas públicas de LFW que más se te parecen." }) : null));
    const user = userEvent.setup();
    renderAt("/famosos");
    const picker = await screen.findByLabelText("Elegir foto");
    await user.upload(picker, new File(["jpeg"], "yo.jpg", { type: "image/jpeg" }));
    expect(await screen.findByText(/Eileen Collins/)).toBeTruthy();
    expect(screen.getByText(/61 % de parecido/)).toBeTruthy();
    const photo = screen.getByRole("img", { name: "Foto de Eileen Collins en LFW" }).getAttribute("src") ?? "";
    expect(new URL(photo, "http://app.test").pathname).toBe("/api/lookalikes/7/photo");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    const query = requests.find((request) => request.method === "POST");
    expect(new URL(query?.url ?? "").pathname).toBe("/api/lookalikes");
    expect(screen.getByRole("img", { name: "La foto que enviaste" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Tomar otra/ })).toBeTruthy();
  });

  it("opens the camera for the selfie and says how to get out when it cannot be used", async () => {
    service();
    const user = userEvent.setup();
    renderAt("/famosos");
    await user.click(await screen.findByRole("button", { name: /Tomar foto/ }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Elegir una foto en su lugar" }));
    expect(await screen.findByLabelText("Elegir foto")).toBeTruthy();
  });

  it("answers an unknown path with a way back", async () => {
    service();
    renderAt("/no-existe");
    expect(await screen.findByRole("heading", { name: "Esta dirección no existe" })).toBeTruthy();
  });
});

describe("recognition overlay", () => {
  it("captions known, unknown and far faces differently", () => {
    const known = faceCaption(face("known", "Ana", 0.71));
    expect([known.text, known.score, known.spoken].map((text) => text?.replace(/\s/g, " "))).toEqual(["Ana", "71 %", "Ana, parecido 71 %"]);
    expect(faceCaption(face("unknown", null, 0.2)).text).toBe("desconocido");
    expect(faceCaption({ ...face("too_small", null, null), label: "acércate" }).text).toBe("acércate");
  });

  it("summarizes who is in front of the camera", () => {
    expect(recognitionSummary([])).toBe("Apunta la cámara a una cara.");
    expect(recognitionSummary([face("known", "Ana", 0.7), face("unknown", null, 0.1), face("unknown", null, 0.1)])).toBe("Ana · 2 desconocidos");
  });

  it("says when the names came from the tracks and nothing was searched", () => {
    const timings = { wait: 0, decode: 2, detect: 4, track: 1, embed: 15, search: 3, total: 25 };
    expect(timingsSummary(timings, [face("known", "Ana", 0.7)])).toBe("Servidor 25 ms · detectar 4 ms · buscar 18 ms");
    const cached = { ...face("known", "Ana", 0.7), cached: true };
    expect(timingsSummary({ ...timings, embed: 0, search: 0 }, [cached])).toBe("Servidor 25 ms · detectar 4 ms · nombre del rastro, sin buscar");
    expect(timingsSummary(timings, [cached, face("unknown", null, 0.2)])).toContain("buscar 18 ms");
  });

  it("draws a box and a label per face, mirrored with the front camera", () => {
    const answer = {
      result: { type: "result" as const, frame: 1, dropped: 0, faces: [face("known", "Ana", 0.7)], timings: { wait: 0, decode: 0, detect: 0, track: 0, embed: 0, search: 0, total: 0 } },
      size: { width: 640, height: 480 },
      roundTripMs: 20,
    };
    const { container } = render(<FaceOverlay answer={answer} view={{ width: 640, height: 480 }} mirrored stale={false} />);
    const box = container.querySelector("rect");
    expect(box?.getAttribute("data-status")).toBe("known");
    expect(Number(box?.getAttribute("x"))).toBe(640 - 100 - 200);
    expect(screen.getByLabelText(/^Ana, parecido 70\s%$/)).toBeTruthy();
  });
});
