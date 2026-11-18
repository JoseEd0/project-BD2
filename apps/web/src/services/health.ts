/**
 * Qué significa la respuesta de `/health` de un servicio para quien usa el portal.
 *
 * Todo servicio de la plataforma responde `{"status": "ok", ...}` en `/health`. Cualquier
 * otra cosa (sin red, un error HTTP, una página HTML que devolvió un servidor equivocado)
 * se muestra como no disponible, con el motivo.
 */

const HEALTHY_STATUS = "ok";

export type UnavailableReason = "unreachable" | "timeout" | "http-error" | "unexpected-body";

export type ServiceHealth =
  | {
      readonly state: "available";
      readonly version: string | null;
      readonly serverTiming: string | null;
    }
  | {
      readonly state: "unavailable";
      readonly reason: UnavailableReason;
      readonly httpStatus: number | null;
    };

const TIMEOUT_ERROR = "TimeoutError";
const SYNTAX_ERROR = "SyntaxError";
const SUCCESS_STATUS_MIN = 200;
const SUCCESS_STATUS_MAX = 299;

function unavailable(reason: UnavailableReason, httpStatus: number | null = null): ServiceHealth {
  return { state: "unavailable", reason, httpStatus };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** La salud según el código y el cuerpo ya leído de la respuesta. */
export function healthFromResponse(
  httpStatus: number,
  body: unknown,
  serverTiming: string | null,
): ServiceHealth {
  if (httpStatus < SUCCESS_STATUS_MIN || httpStatus > SUCCESS_STATUS_MAX) {
    return unavailable("http-error", httpStatus);
  }
  if (!isRecord(body) || body.status !== HEALTHY_STATUS) {
    return unavailable("unexpected-body", httpStatus);
  }
  return {
    state: "available",
    version: typeof body.version === "string" ? body.version : null,
    serverTiming,
  };
}

/** Por nombre y no con `instanceof`: el `DOMException` de un `AbortSignal` puede venir de
 * otro contexto (un iframe, un worker, el entorno de los tests). */
function errorName(error: unknown): string | null {
  return isRecord(error) && typeof error.name === "string" ? error.name : null;
}

/** La salud cuando la petición ni siquiera trajo una respuesta que leer. */
export function healthFromFailure(error: unknown): ServiceHealth {
  const name = errorName(error);
  if (name === TIMEOUT_ERROR) {
    return unavailable("timeout");
  }
  if (name === SYNTAX_ERROR) {
    return unavailable("unexpected-body");
  }
  return unavailable("unreachable");
}

export const UNAVAILABLE_MESSAGES: Record<UnavailableReason, string> = {
  unreachable: "No hay respuesta: el servicio no está desplegado en este entorno o está caído.",
  timeout: "El servicio tardó demasiado en responder.",
  "http-error": "El servicio respondió con un error.",
  "unexpected-body": "Quien respondió no es el servicio: no está desplegado detrás de esta dirección.",
};
