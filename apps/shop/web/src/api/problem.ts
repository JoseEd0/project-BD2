/** Por qué una petición no dio lo que se pidió, en palabras de la tienda. */
export type ProblemKind = "unavailable" | "rejected" | "not-found" | "failed";

const UNAVAILABLE_STATUSES = new Set([502, 503, 504]);
const NOT_FOUND = 404;
const TOO_LARGE = 413;
const TOO_MANY_REQUESTS = 429;
const FIRST_CLIENT_ERROR = 400;
const FIRST_SERVER_ERROR = 500;

interface Wording {
  readonly title: string;
  readonly detail: string;
}

/** Lo que el API dice con su `kind` y la tienda explica a su manera. */
const BY_KIND: Readonly<Record<string, Wording>> = {
  BooleanQueryError: {
    title: "La búsqueda tiene un operador suelto",
    detail: "AND, OR y NOT en mayúsculas y los paréntesis forman una consulta: revisa que cada uno tenga a qué unirse, o escríbelos en minúsculas.",
  },
  InvalidPriceRangeError: {
    title: "El precio mínimo es mayor que el máximo",
    detail: "Corrige el rango de precio o quítalo.",
  },
  UploadTooLargeError: { title: "La foto pesa demasiado", detail: "Prueba con una foto más liviana." },
  TooManyPixelsError: { title: "La foto es demasiado grande", detail: "Prueba con una foto de menos de 40 megapíxeles." },
  UnreadablePhotoError: { title: "No pudimos leer esa foto", detail: "Sube un JPEG, PNG o WebP." },
  UnknownProductError: { title: "Ese producto no está en el catálogo", detail: "Vuelve al catálogo y búscalo de nuevo." },
  UnknownUserError: { title: "Ese cliente no existe", detail: "Elige otro cliente en la barra de arriba." },
  EngineBusyError: {
    title: "La tienda está ocupada",
    detail: "El catálogo se está actualizando en este momento. Lo intentamos de nuevo en unos segundos.",
  },
  StatementTimeoutError: {
    title: "La búsqueda tardó demasiado",
    detail: "El catálogo está muy ocupado. Vuelve a intentarlo en unos segundos.",
  },
  EngineUnavailableError: {
    title: "La tienda no responde",
    detail: "El gestor de la tienda no está disponible ahora. Vuelve a intentarlo en unos segundos.",
  },
};

/** Lo que el gestor dice cuando está ocupado: pasa solo, así que se reintenta. */
const TRANSIENT_KINDS = new Set(["EngineBusyError", "StatementTimeoutError", "EngineUnavailableError"]);

const UNAVAILABLE: Wording = {
  title: "La tienda no responde",
  detail: "No hay conexión con el servicio de la tienda. Revisa tu conexión y vuelve a intentarlo.",
};

/** Un fallo de una petición al API, listo para mostrarse. */
export class ApiProblem extends Error {
  constructor(
    readonly kind: ProblemKind,
    readonly title: string,
    readonly detail: string,
    /** El código HTTP, o `null` si no hubo respuesta. */
    readonly status: number | null,
    /** El `kind` del cuerpo del API, si lo trajo. */
    readonly apiKind: string | null = null,
  ) {
    super(title);
    this.name = "ApiProblem";
  }

  get retryable(): boolean {
    return this.kind === "unavailable" || this.kind === "failed" || (this.apiKind !== null && TRANSIENT_KINDS.has(this.apiKind));
  }
}

function field(body: unknown, name: string): string | null {
  if (typeof body !== "object" || body === null || !(name in body)) {
    return null;
  }
  const value = (body as Record<string, unknown>)[name];
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/** El mensaje del API con mayúscula y punto final. */
export function sentence(text: string): string {
  const capitalised = text.charAt(0).toLocaleUpperCase("es") + text.slice(1);
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

function kindOf(status: number): ProblemKind {
  if (UNAVAILABLE_STATUSES.has(status)) {
    return "unavailable";
  }
  if (status === NOT_FOUND) {
    return "not-found";
  }
  return status >= FIRST_CLIENT_ERROR && status < FIRST_SERVER_ERROR ? "rejected" : "failed";
}

function fallbackWording(status: number, message: string | null): Wording {
  if (UNAVAILABLE_STATUSES.has(status)) {
    return UNAVAILABLE;
  }
  if (status === TOO_LARGE) {
    return { title: "La foto pesa demasiado", detail: "El servidor no acepta archivos tan grandes: prueba con una más liviana." };
  }
  if (status === TOO_MANY_REQUESTS) {
    return { title: "Demasiadas peticiones seguidas", detail: "Espera unos segundos y vuelve a intentarlo." };
  }
  if (status === NOT_FOUND) {
    return { title: "No lo encontramos", detail: message ?? "Lo que buscas ya no está en el catálogo." };
  }
  if (status >= FIRST_SERVER_ERROR) {
    return { title: "La tienda falló", detail: "Vuelve a intentarlo; si se repite, el servicio está fallando." };
  }
  return { title: "La tienda no aceptó la petición", detail: message ?? "Revisa lo que escribiste y vuelve a intentarlo." };
}

/** El problema de una respuesta fallida, con el motivo del API si lo trae. */
export function problemOf(status: number, body: unknown): ApiProblem {
  const apiKind = field(body, "kind");
  const message = field(body, "error");
  const known = apiKind === null ? undefined : BY_KIND[apiKind];
  const wording = known ?? fallbackWording(status, message === null ? null : sentence(message));
  return new ApiProblem(kindOf(status), wording.title, wording.detail, status, apiKind);
}

/** Sin respuesta: la red o el servicio no están. */
export function unreachableProblem(): ApiProblem {
  return new ApiProblem("unavailable", UNAVAILABLE.title, UNAVAILABLE.detail, null);
}

/** Cualquier error de una petición como `ApiProblem`: un `fetch` que no llegó es «no responde». */
export function asProblem(error: unknown): ApiProblem {
  return error instanceof ApiProblem ? error : unreachableProblem();
}
