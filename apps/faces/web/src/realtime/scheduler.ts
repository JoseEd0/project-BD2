import { nextFrameNumber } from "./protocol";

export interface SchedulerOptions {
  /** Cuadros enviados sin respuesta a la vez; el siguiente espera. Nunca se encola. */
  readonly maxInFlight: number;
  /** Un cuadro sin respuesta pasado este plazo se da por perdido y libera su lugar. */
  readonly frameTimeoutMs: number;
  /** Con el tiempo de ida y vuelta suavizado por encima de esto, la red está lenta. */
  readonly slowRoundTripMs: number;
  /** Peso de la última medida en la media móvil exponencial del tiempo de ida y vuelta. */
  readonly roundTripSmoothing: number;
}

/**
 * Decide cuándo sale el siguiente cuadro: a lo sumo `maxInFlight` sin respuesta y no antes
 * del intervalo que pide el servidor (`1 / max_fps`). El servidor procesa siempre el más
 * nuevo y descarta los que esperaban, así que una respuesta libera también los cuadros
 * anteriores a ella. Mide el tiempo de ida y vuelta por número de cuadro, sin relojes
 * compartidos.
 */
export class FrameScheduler {
  private readonly inFlight = new Map<number, number>();
  private lastFrame = 0;
  private lastSentAt = Number.NEGATIVE_INFINITY;
  private minIntervalMs = 0;
  private smoothedRoundTrip: number | null = null;

  constructor(private readonly options: SchedulerOptions) {}

  setMaxFps(maxFps: number): void {
    this.minIntervalMs = maxFps > 0 ? 1000 / maxFps : 0;
  }

  /** Milisegundos hasta poder enviar; 0 si ya se puede. */
  waitMs(now: number): number {
    this.expire(now);
    const intervalWait = Math.max(0, this.lastSentAt + this.minIntervalMs - now);
    if (this.inFlight.size < this.options.maxInFlight) {
      return intervalWait;
    }
    const oldest = Math.min(...this.inFlight.values());
    return Math.max(intervalWait, oldest + this.options.frameTimeoutMs - now);
  }

  canSend(now: number): boolean {
    return this.waitMs(now) === 0;
  }

  /** Reserva el número del cuadro que sale ahora. */
  send(now: number): number {
    this.lastFrame = nextFrameNumber(this.lastFrame);
    this.inFlight.set(this.lastFrame, now);
    this.lastSentAt = now;
    return this.lastFrame;
  }

  /** El tiempo de ida y vuelta del cuadro respondido, o `null` si ya no se esperaba. */
  answered(frame: number, now: number): number | null {
    const sentAt = this.inFlight.get(frame);
    for (const pending of [...this.inFlight.keys()]) {
      if (pending <= frame) {
        this.inFlight.delete(pending);
      }
    }
    if (sentAt === undefined) {
      return null;
    }
    const roundTrip = now - sentAt;
    const weight = this.options.roundTripSmoothing;
    this.smoothedRoundTrip =
      this.smoothedRoundTrip === null ? roundTrip : weight * roundTrip + (1 - weight) * this.smoothedRoundTrip;
    return roundTrip;
  }

  /** Un cuadro que el servidor rechazó (o `null`: no dijo cuál) ya no ocupa lugar. */
  failed(frame: number | null): void {
    if (frame === null) {
      this.inFlight.clear();
      return;
    }
    this.inFlight.delete(frame);
  }

  roundTripMs(): number | null {
    return this.smoothedRoundTrip;
  }

  /** Lenta si la media lo es o si el cuadro más viejo ya tarda más que el umbral. */
  isSlow(now: number): boolean {
    if (this.smoothedRoundTrip !== null && this.smoothedRoundTrip > this.options.slowRoundTripMs) {
      return true;
    }
    return [...this.inFlight.values()].some((sentAt) => now - sentAt > this.options.slowRoundTripMs);
  }

  inFlightCount(): number {
    return this.inFlight.size;
  }

  /** Una conexión nueva: lo que estaba en vuelo no va a responderse. */
  reset(): void {
    this.inFlight.clear();
    this.lastSentAt = Number.NEGATIVE_INFINITY;
    this.smoothedRoundTrip = null;
  }

  private expire(now: number): void {
    for (const [frame, sentAt] of [...this.inFlight]) {
      if (now - sentAt >= this.options.frameTimeoutMs) {
        this.inFlight.delete(frame);
      }
    }
  }
}
