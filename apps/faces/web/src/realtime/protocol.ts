/**
 * El protocolo de `WS /ws/recognize` (ver `apps/faces/README.md`, *Reconocimiento en vivo*).
 * El celular manda cuadros binarios —5 bytes de cabecera (versión y número del cuadro, entero
 * sin signo big-endian) y un JPEG— y recibe JSON: `ready`, un `result` por cuadro procesado y
 * `error` sin que se corte la conexión.
 */

export const PROTOCOL_VERSION = 1;
export const FRAME_HEADER_BYTES = 5;
/** El número de cuadro ocupa 4 bytes sin signo: al llegar al tope vuelve a 1. */
export const MAX_FRAME_NUMBER = 0xffff_ffff;

/** Los cierres con los que el servidor dice por qué cortó. */
export const CLOSE_CODES = {
  normal: 1000,
  goingAway: 1001,
  abnormal: 1006,
  internalError: 1011,
  tryAgainLater: 1013,
  idle: 4408,
} as const;

export type MatchStatus = "known" | "unknown" | "too_small";

export interface FaceBox {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface StreamFace {
  readonly track: number | null;
  readonly box: FaceBox;
  readonly status: MatchStatus;
  readonly label: string;
  readonly displayName: string | null;
  readonly score: number | null;
  readonly cached: boolean;
}

export interface StreamLimits {
  readonly maxFrameBytes: number;
  readonly maxFramePixels: number;
  readonly maxFps: number;
  readonly idleTimeoutSeconds: number;
}

export interface StageTimings {
  readonly wait: number;
  readonly decode: number;
  readonly detect: number;
  readonly track: number;
  readonly embed: number;
  readonly search: number;
  readonly total: number;
}

export interface ReadyMessage {
  readonly type: "ready";
  readonly session: string;
  readonly trackCache: boolean;
  readonly limits: StreamLimits;
}

export interface ResultMessage {
  readonly type: "result";
  readonly frame: number;
  readonly faces: readonly StreamFace[];
  readonly dropped: number;
  readonly timings: StageTimings;
}

export interface ErrorMessage {
  readonly type: "error";
  readonly frame: number | null;
  readonly kind: string;
  readonly error: string;
}

export type ServerMessage = ReadyMessage | ResultMessage | ErrorMessage;

export function nextFrameNumber(previous: number): number {
  return previous >= MAX_FRAME_NUMBER ? 1 : previous + 1;
}

/** El mensaje binario de un cuadro: cabecera y la imagen tal cual. */
export function encodeFrame(frame: number, image: Uint8Array): Uint8Array<ArrayBuffer> {
  if (!Number.isInteger(frame) || frame < 0 || frame > MAX_FRAME_NUMBER) {
    throw new RangeError(`El número de cuadro ${frame} no cabe en 4 bytes sin signo.`);
  }
  const message = new Uint8Array(FRAME_HEADER_BYTES + image.byteLength);
  const header = new DataView(message.buffer);
  header.setUint8(0, PROTOCOL_VERSION);
  header.setUint32(1, frame, false);
  message.set(image, FRAME_HEADER_BYTES);
  return message;
}

/** Retoma los rastros de una sesión anterior al reconectarse. */
export function helloMessage(session: string | null, trackCache = true): string {
  return JSON.stringify({ type: "hello", session, track_cache: trackCache });
}

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function numberOr(value: unknown, fallback: number): number {
  return isNumber(value) ? value : fallback;
}

function parseBox(value: unknown): FaceBox | null {
  if (!isRecord(value) || !isNumber(value.x) || !isNumber(value.y) || !isNumber(value.width) || !isNumber(value.height)) {
    return null;
  }
  return { x: value.x, y: value.y, width: value.width, height: value.height };
}

const MATCH_STATUSES: readonly string[] = ["known", "unknown", "too_small"];

function parseFace(value: unknown): StreamFace | null {
  if (!isRecord(value) || typeof value.status !== "string" || !MATCH_STATUSES.includes(value.status)) {
    return null;
  }
  const box = parseBox(value.box);
  if (box === null) {
    return null;
  }
  return {
    track: isNumber(value.track) ? value.track : null,
    box,
    status: value.status as MatchStatus,
    label: typeof value.label === "string" ? value.label : "",
    displayName: typeof value.display_name === "string" ? value.display_name : null,
    score: isNumber(value.score) ? value.score : null,
    cached: value.cached === true,
  };
}

function parseTimings(value: unknown): StageTimings {
  const timings = isRecord(value) ? value : {};
  return {
    wait: numberOr(timings.wait, 0),
    decode: numberOr(timings.decode, 0),
    detect: numberOr(timings.detect, 0),
    track: numberOr(timings.track, 0),
    embed: numberOr(timings.embed, 0),
    search: numberOr(timings.search, 0),
    total: numberOr(timings.total, 0),
  };
}

function parseReady(message: Json): ReadyMessage | null {
  if (typeof message.session !== "string" || !isRecord(message.limits)) {
    return null;
  }
  const limits = message.limits;
  if (!isNumber(limits.max_fps) || limits.max_fps <= 0) {
    return null;
  }
  return {
    type: "ready",
    session: message.session,
    trackCache: message.track_cache === true,
    limits: {
      maxFrameBytes: numberOr(limits.max_frame_bytes, Number.POSITIVE_INFINITY),
      maxFramePixels: numberOr(limits.max_frame_pixels, Number.POSITIVE_INFINITY),
      maxFps: limits.max_fps,
      idleTimeoutSeconds: numberOr(limits.idle_timeout_seconds, Number.POSITIVE_INFINITY),
    },
  };
}

function parseResult(message: Json): ResultMessage | null {
  if (!isNumber(message.frame) || !Array.isArray(message.faces)) {
    return null;
  }
  const faces = message.faces.map(parseFace);
  if (faces.some((face) => face === null)) {
    return null;
  }
  return {
    type: "result",
    frame: message.frame,
    faces: faces as StreamFace[],
    dropped: numberOr(message.dropped, 0),
    timings: parseTimings(message.timings),
  };
}

function parseError(message: Json): ErrorMessage {
  return {
    type: "error",
    frame: isNumber(message.frame) ? message.frame : null,
    kind: typeof message.kind === "string" ? message.kind : "UnknownError",
    error: typeof message.error === "string" ? message.error : "",
  };
}

/** Un mensaje de texto del servidor, o `null` si no es uno del protocolo. */
export function parseServerMessage(text: string): ServerMessage | null {
  let message: unknown;
  try {
    message = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(message)) {
    return null;
  }
  switch (message.type) {
    case "ready":
      return parseReady(message);
    case "result":
      return parseResult(message);
    case "error":
      return parseError(message);
    default:
      return null;
  }
}
