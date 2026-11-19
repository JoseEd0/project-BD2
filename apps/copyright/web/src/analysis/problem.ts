/** Por qué no hay resultado: el archivo se rechazó, el servicio no está o falló. */
export type ProblemKind = "rejected" | "unavailable" | "failed";

export interface Problem {
  readonly kind: ProblemKind;
  readonly title: string;
  readonly detail: string;
  /** El código HTTP, o `null` si no hubo respuesta. */
  readonly status: number | null;
}

const UNAVAILABLE_STATUSES = new Set([502, 503, 504]);
const TOO_MANY_REQUESTS = 429;
const FIRST_SERVER_ERROR = 500;

const UNAVAILABLE: Omit<Problem, "status"> = {
  kind: "unavailable",
  title: "El servicio no responde",
  detail: "La detección no está disponible ahora. Vuelve a intentarlo en un momento.",
};

const REJECTIONS: Readonly<Record<number, Omit<Problem, "status">>> = {
  400: { kind: "rejected", title: "No pudimos leer ese audio", detail: "El archivo está vacío, dañado o dura menos de un segundo." },
  413: { kind: "rejected", title: "El audio es demasiado grande", detail: "Se admiten hasta 48 MB y 10 minutos: recórtalo o súbelo comprimido." },
  415: { kind: "rejected", title: "Ese archivo no es un audio que podamos leer", detail: "Sube un MP3, WAV, FLAC u OGG." },
  422: { kind: "rejected", title: "La subida llegó sin archivo", detail: "Elige el audio otra vez." },
};

/** El mensaje del API (`{"error": …}`), con mayúscula y punto, si lo trae. */
export function apiMessage(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return null;
  }
  const message = (body as { error: unknown }).error;
  if (typeof message !== "string" || message.trim() === "") {
    return null;
  }
  const trimmed = message.trim();
  const capitalised = trimmed.charAt(0).toLocaleUpperCase("es") + trimmed.slice(1);
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

/** El problema de una respuesta fallida de `POST /detections`, con el motivo del API. */
export function problemOf(status: number, body: unknown): Problem {
  if (UNAVAILABLE_STATUSES.has(status)) {
    return { ...UNAVAILABLE, status };
  }
  const known = REJECTIONS[status];
  if (known !== undefined) {
    return { ...known, detail: apiMessage(body) ?? known.detail, status };
  }
  if (status === TOO_MANY_REQUESTS) {
    return { kind: "failed", title: "Demasiadas subidas seguidas", detail: "Espera unos segundos y vuelve a intentarlo.", status };
  }
  const detail = apiMessage(body) ?? "Vuelve a intentarlo; si se repite, el servicio está fallando.";
  return { kind: "failed", title: status >= FIRST_SERVER_ERROR ? "El servicio falló al analizar" : "La subida no se aceptó", detail, status };
}

/** Sin respuesta: la red o el servicio no están. */
export function unreachableProblem(): Problem {
  return { ...UNAVAILABLE, status: null };
}
