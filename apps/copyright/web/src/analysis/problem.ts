/** Por qué no hay resultado: el archivo se rechazó, el servicio no está o falló. */
export type ProblemKind = "rejected" | "unavailable" | "loading" | "busy" | "failed";

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

/*
 * Mensajes del gestor que no son culpa de quien sube: el catálogo aún no tiene el HNSW de sus
 * tramos (la carga sigue) o está tomado por otra escritura. Se reconocen por su texto porque
 * llegan como un 400 genérico del gestor.
 */
const CATALOG_LOADING_PATTERN = /índice HNSW|no existe la tabla|no hay una tabla|cr_segmentos/i;
const CATALOG_BUSY_PATTERN = /ocupado/i;

const CATALOG_LOADING: Omit<Problem, "status"> = {
  kind: "loading",
  title: "El catálogo se está cargando",
  detail: "Todavía se están indexando las canciones originales. Vuelve a intentarlo en unos minutos.",
};

const CATALOG_BUSY: Omit<Problem, "status"> = {
  kind: "busy",
  title: "El catálogo está ocupado",
  detail: "Se está escribiendo en el catálogo en este momento. Reintenta en unos segundos.",
};

function rawMessage(body: unknown): string {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return "";
  }
  const message = (body as { error: unknown }).error;
  return typeof message === "string" ? message : "";
}

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
  const message = rawMessage(body);
  if (CATALOG_BUSY_PATTERN.test(message)) {
    return { ...CATALOG_BUSY, status };
  }
  if (CATALOG_LOADING_PATTERN.test(message)) {
    return { ...CATALOG_LOADING, status };
  }
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
