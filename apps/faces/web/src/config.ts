import { APP } from "./identity";
import type { JpegOptions } from "./camera/capture";
import type { SchedulerOptions } from "./realtime/scheduler";

/**
 * Dónde está el API, desde las variables de entorno de Vite (ver `.env.example`). Vacía o
 * ausente, `VITE_API_BASE_URL` es el prefijo del API en el mismo origen de la interfaz.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || APP.apiPrefix;

export const RECOGNIZE_PATH = "/ws/recognize";

/**
 * El puerto HTTP de la web en el compose y el HTTPS que lo acompaña (`FACES_WEB_PORT` y
 * `FACES_WEB_TLS_PORT` por omisión): sin HTTPS no hay cámara, así que se ofrece el salto.
 */
export const SECURE_PORT_BY_HTTP_PORT: Readonly<Record<string, string>> = { "8104": "8404" };

/** Un cuadro de 640 px al 70 %: unos 40 KB, lejos del tope de 1 MiB, y el rostro se sigue viendo. */
export const STREAM_JPEG: JpegOptions = { maxSide: 640, quality: 0.7 };
/** Una toma del registro: más detalle, porque se juzga su nitidez y el rostro debe pasar de 80 px. */
export const SHOT_JPEG: JpegOptions = { maxSide: 1280, quality: 0.9 };

export const RECOGNITION_SCHEDULER: SchedulerOptions = {
  maxInFlight: 2,
  frameTimeoutMs: 4_000,
  slowRoundTripMs: 700,
  roundTripSmoothing: 0.2,
};

/** Durante el registro el socket solo dice dónde está la cara: basta uno en vuelo. */
export const GUIDE_SCHEDULER: SchedulerOptions = { ...RECOGNITION_SCHEDULER, maxInFlight: 1 };

/** La URL del socket: la misma base del API, con `ws:` o `wss:` según la página. */
export function streamUrl(apiBase: string, location: Pick<Location, "protocol" | "host">): string {
  if (/^https?:\/\//i.test(apiBase)) {
    return apiBase.replace(/^http/i, "ws").replace(/\/+$/, "") + RECOGNIZE_PATH;
  }
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${apiBase.replace(/\/+$/, "")}${RECOGNIZE_PATH}`;
}
