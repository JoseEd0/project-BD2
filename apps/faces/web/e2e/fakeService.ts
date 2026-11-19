import type { BrowserContext, Page, WebSocketRoute } from "@playwright/test";

export const PERSON_ID = "0123456789abcdef0123456789abcdef";
const ENROLLMENT_ID = "e".repeat(32);
const SESSION = "5".repeat(32);
const POSES = ["front", "left", "right"] as const;
type Pose = (typeof POSES)[number];

const GUIDANCE: Record<Pose, string> = {
  front: "Mira directo a la cámara.",
  left: "Gira un poco la cabeza hacia tu izquierda.",
  right: "Gira un poco la cabeza hacia tu derecha.",
};

export const CONSENT = {
  version: "1-abcdef123456",
  title: "Consentimiento para registrar tu rostro",
  purpose: "Reconocerte en esta demostración de reconocimiento facial del curso Base de Datos 2.",
  stored_data: ["El nombre que escribas.", "Una huella numérica de tu rostro por cada toma.", "La versión de este texto y la fecha."],
  not_stored: "Tus fotos: se analizan en memoria y se descartan en el momento.",
  retention_days: 30,
  retention: "Tu registro se borra solo a los 30 días de creado, o antes si lo pides.",
  rights: "Puedes ver y borrar tu registro cuando quieras con el código que recibes al terminar.",
  text: "…",
};

function enrollment(name: string, covered: readonly Pose[]) {
  const remaining = POSES.filter((pose) => !covered.includes(pose));
  return {
    enrollment_id: ENROLLMENT_ID,
    display_name: name,
    poses: POSES,
    covered,
    remaining,
    attempts_left: 20 - covered.length,
    expires_at: "2030-01-01T00:15:00Z",
    message: remaining[0] === undefined ? "Ya tenemos todas las tomas." : GUIDANCE[remaining[0]],
  };
}

function person(name: string) {
  return {
    person_id: PERSON_ID,
    display_name: name,
    consent_version: CONSENT.version,
    consented_at: "2030-01-01T00:00:00Z",
    created_at: "2030-01-01T00:01:00Z",
    expires_at: "2030-01-31T00:01:00Z",
    shots: 3,
  };
}

export interface ServiceScript {
  /** Lo que responde cada toma, en orden; sin más guion, se aceptan las poses que faltan. */
  readonly shots?: readonly ({ readonly status: number; readonly body: unknown } | "accept" | { readonly reject: string; readonly message: string })[];
}

/** El API simulado del servicio de rostros, con su estado (el registro y la persona). */
export async function fakeService(context: BrowserContext, script: ServiceScript = {}) {
  const state = { name: "", covered: [] as Pose[], shots: 0, deleted: [] as string[], personHeader: [] as string[], enrolled: false };
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/health") {
      return route.fulfill({ json: { status: "ok", version: "0.1.0" } });
    }
    if (path === "/consent") {
      return route.fulfill({ json: CONSENT });
    }
    if (path === "/enrollments" && method === "POST") {
      const body = request.postDataJSON() as { display_name: string; consent: { version: string; accepted: boolean } };
      state.name = body.display_name;
      state.covered = [];
      return route.fulfill({ status: 201, json: enrollment(state.name, []) });
    }
    if (path.endsWith("/shots")) {
      const scripted = script.shots?.[state.shots];
      state.shots += 1;
      if (scripted !== undefined && scripted !== "accept") {
        if ("reject" in scripted) {
          return route.fulfill({ json: { accepted: false, reason: scripted.reject, message: scripted.message, pose: null, quality: null, enrollment: enrollment(state.name, state.covered) } });
        }
        return route.fulfill({ status: scripted.status, json: scripted.body });
      }
      const pose = POSES.find((candidate) => !state.covered.includes(candidate)) ?? "front";
      state.covered = [...state.covered, pose];
      return route.fulfill({ json: { accepted: true, reason: null, message: "Toma aceptada.", pose, quality: null, enrollment: enrollment(state.name, state.covered) } });
    }
    if (path.endsWith("/finish")) {
      state.enrolled = true;
      return route.fulfill({ status: 201, json: { person: person(state.name), message: `Listo, ${state.name}: ya estás registrado.` } });
    }
    if (path.startsWith("/enrollments/") && method === "DELETE") {
      return route.fulfill({ status: 204 });
    }
    if (path === "/person") {
      const code = (await request.headerValue("x-person-id")) ?? "";
      state.personHeader.push(code);
      if (method === "DELETE") {
        state.deleted.push(code);
        state.enrolled = false;
        return route.fulfill({ status: 204 });
      }
      return state.enrolled
        ? route.fulfill({ json: person(state.name) })
        : route.fulfill({ status: 404, json: { error: "No hay un registro con ese código, o ya venció.", kind: "PersonNotFoundError", request_id: "r" } });
    }
    return route.fulfill({ status: 404, json: { error: "no", kind: "NotFound", request_id: "r" } });
  });
  return state;
}

/** Ancho y alto de un JPEG, leídos de su marcador SOF: las cajas van en los píxeles del cuadro. */
export function jpegSize(bytes: Buffer): { width: number; height: number } {
  let offset = 2;
  while (offset + 9 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = bytes[offset + 1] ?? 0;
    const length = bytes.readUInt16BE(offset + 2);
    if (marker >= 0xc0 && marker <= 0xc3) {
      return { height: bytes.readUInt16BE(offset + 5), width: bytes.readUInt16BE(offset + 7) };
    }
    offset += 2 + length;
  }
  throw new Error("JPEG sin SOF");
}

export type FaceAnswer = (frame: { width: number; height: number }) => readonly unknown[];

/** Una cara centrada, del tamaño de una selfie, con el estado y nombre que se pidan. */
export function centeredFace(status: "known" | "unknown" | "too_small", displayName: string | null = null, score: number | null = null): FaceAnswer {
  return ({ width, height }) => {
    const side = width * 0.32;
    return [
      {
        track: 1,
        box: { x: (width - side) / 2, y: height * 0.42 - side * 0.6, width: side, height: side * 1.2 },
        status,
        label: displayName ?? (status === "too_small" ? "acércate" : "desconocido"),
        display_name: displayName,
        score,
        cached: false,
      },
    ];
  };
}

export interface SocketScript {
  faces: FaceAnswer;
  /** Si se define, la primera conexión se cierra con este código al llegar. */
  closeFirstWith?: number;
}

/** El socket del reconocimiento simulado: `ready` y un `result` por cada cuadro con su número. */
export async function fakeSocket(page: Page, script: SocketScript) {
  const log = { connections: 0, frames: 0, hellos: [] as unknown[], sockets: [] as WebSocketRoute[] };
  await page.routeWebSocket(/\/api\/ws\/recognize$/, (socket) => {
    log.connections += 1;
    log.sockets.push(socket);
    if (script.closeFirstWith !== undefined && log.connections === 1) {
      void socket.close({ code: script.closeFirstWith, reason: "" });
      return;
    }
    const ready = { type: "ready", session: SESSION, track_cache: true, limits: { max_frame_bytes: 1 << 20, max_frame_pixels: 2073600, max_fps: 15, idle_timeout_seconds: 30 } };
    socket.send(JSON.stringify(ready));
    socket.onMessage((message) => {
      if (typeof message === "string") {
        log.hellos.push(JSON.parse(message));
        socket.send(JSON.stringify(ready));
        return;
      }
      log.frames += 1;
      const frame = message.readUInt32BE(1);
      const size = jpegSize(message.subarray(5));
      socket.send(
        JSON.stringify({
          type: "result",
          frame,
          dropped: 0,
          faces: script.faces(size),
          timings: { wait: 0.2, decode: 1.1, detect: 6.4, track: 0.5, embed: 9.8, search: 2.1, total: 20.3 },
        }),
      );
    });
  });
  return log;
}
