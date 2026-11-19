import type { FailureKind } from "./failures";

export interface FailureCopy {
  readonly title: string;
  readonly body: string;
  /** Si conviene ofrecer la búsqueda sola cuando el modelo no está: los fragmentos siguen ahí. */
  readonly offerRetrieval: boolean;
}

/** Qué pasó y qué hacer, por categoría. Los textos son de esta app: son su tono. */
export const FAILURE_COPY: Readonly<Record<FailureKind, FailureCopy>> = {
  "answers-unavailable": {
    title: "Las respuestas no están activas en este servidor",
    body: "Falta la clave del modelo de lenguaje. La búsqueda sí funciona: los fragmentos que habría leído el modelo están en la recuperación.",
    offerRetrieval: true,
  },
  "budget-exhausted": {
    title: "Se alcanzó el gasto máximo de hoy",
    body: "El servicio no llama al modelo hasta que empiece el día siguiente (UTC). La búsqueda sigue disponible.",
    offerRetrieval: true,
  },
  "rate-limited": {
    title: "Demasiadas preguntas seguidas",
    body: "El servicio limita las peticiones por segundo. Espera un momento y vuelve a intentarlo.",
    offerRetrieval: false,
  },
  "model-unavailable": {
    title: "El modelo no respondió",
    body: "El proveedor del modelo está caído o sobrecargado. Suele pasar en segundos.",
    offerRetrieval: true,
  },
  "model-misconfigured": {
    title: "El servicio no puede usar el modelo",
    body: "La clave del modelo no sirve o no tiene permiso. Es configuración del servidor, no tu pregunta.",
    offerRetrieval: true,
  },
  "engine-unavailable": {
    title: "La base de datos no respondió",
    body: "El gestor que guarda los papers y sus índices no está disponible. Vuelve a intentarlo en unos segundos.",
    offerRetrieval: false,
  },
  "invalid-question": {
    title: "La pregunta no se pudo enviar",
    body: "Escribe entre 1 y 1 000 caracteres y revisa los filtros.",
    offerRetrieval: false,
  },
  network: {
    title: "Se perdió la conexión",
    body: "La petición no llegó al servicio o su respuesta no llegó hasta aquí.",
    offerRetrieval: false,
  },
  interrupted: {
    title: "La respuesta se cortó a mitad",
    body: "La conexión se cerró antes del final. El texto de arriba está incompleto.",
    offerRetrieval: false,
  },
  unexpected: {
    title: "Algo falló en el servicio",
    body: "El error quedó en el registro del servidor con el identificador de abajo.",
    offerRetrieval: false,
  },
};
