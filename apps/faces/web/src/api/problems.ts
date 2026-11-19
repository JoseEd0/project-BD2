/**
 * Un fallo del API tal como lo usa la interfaz: el `kind` del servicio (el nombre de su
 * excepción) para decidir qué mostrar, y un mensaje en español para la persona.
 */
export interface ApiProblem {
  readonly kind: string;
  readonly message: string;
  readonly status: number | null;
}

export const NETWORK_ERROR = "NetworkError";

const MESSAGES_BY_STATUS: Record<number, string> = {
  413: "La foto pesa demasiado. Inténtalo otra vez.",
  429: "Vas muy rápido: espera un momento y vuelve a intentarlo.",
  502: "El servicio de rostros no responde ahora. Inténtalo en unos segundos.",
  503: "El servicio de rostros está ocupado. Inténtalo en unos segundos.",
  504: "El servicio de rostros tardó demasiado. Inténtalo otra vez.",
};

const GENERIC_MESSAGE = "Algo salió mal en el servicio. Inténtalo otra vez.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** El problema de una respuesta de error: `{error, kind}` del servicio, o uno por su estado
 * cuando respondió otro (el nginx con un 429 o un 502, por ejemplo). */
export function problemFromResponse(status: number, body: unknown): ApiProblem {
  if (isRecord(body) && typeof body.error === "string" && typeof body.kind === "string") {
    return { kind: body.kind, message: body.error, status };
  }
  return { kind: `Http${status}`, message: MESSAGES_BY_STATUS[status] ?? GENERIC_MESSAGE, status };
}

export function networkProblem(): ApiProblem {
  return {
    kind: NETWORK_ERROR,
    message: "No hay conexión con el servicio. Revisa tu red e inténtalo otra vez.",
    status: null,
  };
}
