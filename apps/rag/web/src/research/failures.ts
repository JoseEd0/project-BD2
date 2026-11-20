/**
 * Por qué una pregunta no tuvo respuesta, en las categorías que la interfaz explica de
 * forma distinta: cada una dice qué pasó y qué puede hacer quien pregunta.
 */
export type FailureKind =
  | "answers-unavailable"
  | "budget-exhausted"
  | "rate-limited"
  | "model-unavailable"
  | "model-misconfigured"
  | "engine-unavailable"
  | "engine-busy"
  | "invalid-question"
  | "network"
  | "interrupted"
  | "unexpected";

export interface Failure {
  readonly kind: FailureKind;
  /** El mensaje del servicio, si lo dio; se muestra como detalle, no como titular. */
  readonly detail: string | null;
  readonly requestId: string | null;
  /** Si volver a preguntar lo mismo puede salir bien sin cambiar nada. */
  readonly retryable: boolean;
}

const RETRYABLE: ReadonlySet<FailureKind> = new Set<FailureKind>([
  "rate-limited",
  "model-unavailable",
  "engine-unavailable",
  "engine-busy",
  "network",
  "interrupted",
  "unexpected",
]);

const KIND_BY_ERROR: Readonly<Record<string, FailureKind>> = {
  AnswersUnavailableError: "answers-unavailable",
  BudgetExhaustedError: "budget-exhausted",
  LlmRateLimitedError: "rate-limited",
  LlmUnavailableError: "model-unavailable",
  LlmAccessError: "model-misconfigured",
  LlmRequestError: "model-misconfigured",
  EngineUnavailableError: "engine-unavailable",
  EngineBusyError: "engine-busy",
  RequestValidationError: "invalid-question",
};

const HTTP_TOO_MANY_REQUESTS = 429;
const HTTP_UNPROCESSABLE = 422;
const HTTP_SERVICE_UNAVAILABLE = 503;
const HTTP_BAD_GATEWAY = 502;
const HTTP_GATEWAY_TIMEOUT = 504;

function failure(kind: FailureKind, detail: string | null, requestId: string | null): Failure {
  return { kind, detail, requestId, retryable: RETRYABLE.has(kind) };
}

function kindOfStatus(status: number): FailureKind {
  if (status === HTTP_TOO_MANY_REQUESTS) {
    // Sin `kind` del servicio, el 429 es el límite por IP de nginx.
    return "rate-limited";
  }
  if (status === HTTP_UNPROCESSABLE) {
    return "invalid-question";
  }
  if ([HTTP_SERVICE_UNAVAILABLE, HTTP_BAD_GATEWAY, HTTP_GATEWAY_TIMEOUT].includes(status)) {
    return "engine-unavailable";
  }
  return "unexpected";
}

/** Un error del servicio antes del stream: su estado y el cuerpo JSON del kit, si lo hay. */
export function failureOfResponse(status: number, body: unknown): Failure {
  const parsed = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const kind = typeof parsed.kind === "string" ? parsed.kind : null;
  const detail = typeof parsed.error === "string" ? parsed.error : null;
  const requestId = typeof parsed.request_id === "string" ? parsed.request_id : null;
  const known = kind === null ? undefined : KIND_BY_ERROR[kind];
  return failure(known ?? kindOfStatus(status), detail, requestId);
}

/** Un evento `error` a mitad del stream. */
export function failureOfEvent(kind: string, detail: string, requestId: string | null): Failure {
  return failure(KIND_BY_ERROR[kind] ?? "unexpected", detail, requestId);
}

export function networkFailure(): Failure {
  return failure("network", null, null);
}

/** El cuerpo se cerró sin `done` ni `error`: la conexión se cortó a mitad. */
export function interruptedFailure(): Failure {
  return failure("interrupted", null, null);
}

export function unexpectedFailure(): Failure {
  return failure("unexpected", null, null);
}
