import { CLOSE_CODES } from "./protocol";

export interface BackoffOptions {
  readonly baseMs: number;
  readonly maxMs: number;
  /** Fracción del retraso que se sortea, para que no vuelvan todos los celulares a la vez. */
  readonly jitter: number;
}

export type ReconnectDecision =
  | { readonly kind: "retry"; readonly delayMs: number; readonly reason: "busy" | "lost" }
  | { readonly kind: "wait-for-interaction" }
  | { readonly kind: "stop" };

/** Una réplica llena espera más que una conexión que solo se cortó. */
export const BUSY_BACKOFF: BackoffOptions = { baseMs: 2_000, maxMs: 30_000, jitter: 0.3 };
export const LOST_BACKOFF: BackoffOptions = { baseMs: 500, maxMs: 15_000, jitter: 0.3 };

export function backoffDelay(attempt: number, options: BackoffOptions, random: () => number): number {
  const exponential = Math.min(options.maxMs, options.baseMs * 2 ** Math.max(0, attempt));
  const spread = exponential * options.jitter;
  return Math.round(exponential - spread + 2 * spread * random());
}

/**
 * Qué hacer cuando se cierra el socket: `1000` lo cerramos nosotros; `4408` es una conexión
 * que pasó el tiempo sin cuadros y se retoma cuando la persona vuelve a tocar la pantalla;
 * `1013` es una réplica llena (se reintenta con más espera); cualquier otro cierre es una
 * conexión perdida que se reintenta con una espera que crece.
 */
export function decideReconnect(closeCode: number, attempt: number, random: () => number = Math.random): ReconnectDecision {
  switch (closeCode) {
    case CLOSE_CODES.normal:
      return { kind: "stop" };
    case CLOSE_CODES.idle:
      return { kind: "wait-for-interaction" };
    case CLOSE_CODES.tryAgainLater:
      return { kind: "retry", reason: "busy", delayMs: backoffDelay(attempt, BUSY_BACKOFF, random) };
    default:
      return { kind: "retry", reason: "lost", delayMs: backoffDelay(attempt, LOST_BACKOFF, random) };
  }
}
