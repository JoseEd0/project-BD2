import { decideReconnect } from "./reconnect";
import {
  CLOSE_CODES,
  encodeFrame,
  helloMessage,
  parseServerMessage,
  type ErrorMessage,
  type ResultMessage,
} from "./protocol";
import { FrameScheduler, type SchedulerOptions } from "./scheduler";
import type { Size } from "../camera/geometry";

/**
 * - `connecting`: abriendo el socket (o esperando su `ready`).
 * - `live`: mandando cuadros.
 * - `retrying`: la conexión se cortó y vuelve a intentarlo en `retryInMs`.
 * - `busy`: la réplica está llena (`1013`); también reintenta.
 * - `idle`: el servidor cerró por inactividad (`4408`); sigue cuando la persona toca.
 * - `stopped`: cerrada por la app (pausa o salida).
 */
export type StreamPhase = "connecting" | "live" | "retrying" | "busy" | "idle" | "stopped";

export interface StreamState {
  readonly phase: StreamPhase;
  readonly attempt: number;
  readonly retryInMs: number | null;
  readonly slow: boolean;
  readonly roundTripMs: number | null;
}

export interface CapturedFrame {
  readonly bytes: Uint8Array;
  readonly size: Size;
}

export interface FrameAnswer {
  readonly result: ResultMessage;
  /** El tamaño del cuadro que se envió: las cajas vienen en sus píxeles. */
  readonly size: Size;
  readonly roundTripMs: number | null;
}

export interface Clock {
  now(): number;
  setTimeout(callback: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

export interface StreamOptions {
  readonly url: string;
  readonly scheduler: SchedulerOptions;
  /** El cuadro que sale ahora, o `null` si la cámara todavía no tiene uno. */
  readonly capture: () => Promise<CapturedFrame | null>;
  readonly onState: (state: StreamState) => void;
  readonly onAnswer: (answer: FrameAnswer) => void;
  readonly onFrameError?: (error: ErrorMessage) => void;
  readonly createSocket?: (url: string) => WebSocket;
  readonly clock?: Clock;
  readonly random?: () => number;
  readonly trackCache?: boolean;
}

/** Cada cuánto se vuelve a mirar si la red está lenta mientras no llegan respuestas. */
const SLOW_CHECK_INTERVAL_MS = 500;
/** Nada sale si el socket aún no envió lo anterior: con la subida saturada no se apila. */
const MAX_BUFFERED_BYTES = 0;
const SOCKET_CONNECTING = 0;
const SOCKET_OPEN = 1;

const browserClock: Clock = {
  now: () => performance.now(),
  setTimeout: (callback, ms) => window.setTimeout(callback, ms),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

/**
 * El cliente del reconocimiento en vivo: abre el socket, manda un cuadro solo cuando el
 * planificador lo permite (nunca encola), se reconecta con la sesión para retomar los
 * rastros y decide según el código de cierre si reintenta, espera o se detiene.
 */
export class RecognitionStream {
  private readonly scheduler: FrameScheduler;
  private readonly clock: Clock;
  private readonly random: () => number;
  private readonly frameSizes = new Map<number, Size>();
  private socket: WebSocket | null = null;
  private session: string | null = null;
  private phase: StreamPhase = "stopped";
  private attempt = 0;
  private retryInMs: number | null = null;
  private timer: number | null = null;
  private capturing = false;
  private lastReportedSlow = false;

  constructor(private readonly options: StreamOptions) {
    this.scheduler = new FrameScheduler(options.scheduler);
    this.clock = options.clock ?? browserClock;
    this.random = options.random ?? Math.random;
  }

  start(): void {
    if (this.socket !== null || this.phase === "connecting") {
      return;
    }
    this.attempt = 0;
    this.open();
  }

  /** Cierra sin reintentar. Lo usa la pausa, la pestaña oculta y la salida de la pantalla. */
  stop(): void {
    this.cancelTimer();
    this.closeSocket(CLOSE_CODES.normal);
    this.setPhase("stopped");
  }

  /** Tras un cierre por inactividad, o para reintentar ya sin esperar el turno. */
  resume(): void {
    if (this.phase === "live" || this.phase === "connecting") {
      return;
    }
    this.cancelTimer();
    this.open();
  }

  private open(): void {
    this.closeSocket(CLOSE_CODES.normal);
    this.scheduler.reset();
    this.frameSizes.clear();
    this.retryInMs = null;
    this.setPhase("connecting");
    const socket = (this.options.createSocket ?? ((url) => new WebSocket(url)))(this.options.url);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => this.sendHello(socket);
    socket.onmessage = (event: MessageEvent) => this.receive(socket, event.data);
    socket.onclose = (event: CloseEvent) => this.closed(socket, event.code);
    this.socket = socket;
  }

  private sendHello(socket: WebSocket): void {
    const trackCache = this.options.trackCache ?? true;
    if (this.session !== null || !trackCache) {
      socket.send(helloMessage(this.session, trackCache));
    }
  }

  private receive(socket: WebSocket, data: unknown): void {
    if (socket !== this.socket || typeof data !== "string") {
      return;
    }
    const message = parseServerMessage(data);
    if (message === null) {
      return;
    }
    if (message.type === "ready") {
      this.session = message.session;
      this.scheduler.setMaxFps(message.limits.maxFps);
      this.attempt = 0;
      this.setPhase("live");
    } else if (message.type === "result") {
      this.answer(message);
    } else {
      this.scheduler.failed(message.frame);
      this.options.onFrameError?.(message);
    }
    this.pump();
  }

  private answer(result: ResultMessage): void {
    const size = this.frameSizes.get(result.frame);
    for (const frame of [...this.frameSizes.keys()]) {
      if (frame <= result.frame) {
        this.frameSizes.delete(frame);
      }
    }
    const roundTripMs = this.scheduler.answered(result.frame, this.clock.now());
    if (size !== undefined) {
      this.options.onAnswer({ result, size, roundTripMs });
    }
    this.emitState();
  }

  private closed(socket: WebSocket, code: number): void {
    if (socket !== this.socket) {
      return;
    }
    this.socket = null;
    this.cancelTimer();
    const decision = decideReconnect(code, this.attempt, this.random);
    if (decision.kind === "stop") {
      this.setPhase("stopped");
      return;
    }
    if (decision.kind === "wait-for-interaction") {
      this.setPhase("idle");
      return;
    }
    this.attempt += 1;
    this.retryInMs = decision.delayMs;
    this.setPhase(decision.reason === "busy" ? "busy" : "retrying");
    this.schedule(() => this.open(), decision.delayMs);
  }

  private pump(): void {
    const socket = this.socket;
    if (this.phase !== "live" || socket === null || this.capturing) {
      return;
    }
    const now = this.clock.now();
    const wait = this.scheduler.waitMs(now);
    if (wait > 0 || socket.bufferedAmount > MAX_BUFFERED_BYTES) {
      this.schedule(() => this.pump(), Math.min(Math.max(wait, 1), SLOW_CHECK_INTERVAL_MS));
      this.reportSlowness();
      return;
    }
    this.capturing = true;
    void this.options
      .capture()
      .then((frame) => this.sendFrame(socket, frame))
      .catch(() => {
        this.capturing = false;
        this.schedule(() => this.pump(), SLOW_CHECK_INTERVAL_MS);
      });
  }

  private sendFrame(socket: WebSocket, frame: CapturedFrame | null): void {
    this.capturing = false;
    if (socket !== this.socket || this.phase !== "live") {
      return;
    }
    if (frame === null) {
      this.schedule(() => this.pump(), SLOW_CHECK_INTERVAL_MS);
      return;
    }
    const number = this.scheduler.send(this.clock.now());
    this.frameSizes.set(number, frame.size);
    socket.send(encodeFrame(number, frame.bytes));
    this.pump();
  }

  private reportSlowness(): void {
    if (this.scheduler.isSlow(this.clock.now()) !== this.lastReportedSlow) {
      this.emitState();
    }
  }

  private schedule(callback: () => void, ms: number): void {
    this.cancelTimer();
    this.timer = this.clock.setTimeout(() => {
      this.timer = null;
      callback();
    }, ms);
  }

  private cancelTimer(): void {
    if (this.timer !== null) {
      this.clock.clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private closeSocket(code: number): void {
    const socket = this.socket;
    this.socket = null;
    if (socket === null) {
      return;
    }
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    if (socket.readyState === SOCKET_OPEN || socket.readyState === SOCKET_CONNECTING) {
      socket.close(code);
    }
  }

  private setPhase(phase: StreamPhase): void {
    this.phase = phase;
    this.emitState();
  }

  private emitState(): void {
    const slow = this.phase === "live" && this.scheduler.isSlow(this.clock.now());
    this.lastReportedSlow = slow;
    this.options.onState({
      phase: this.phase,
      attempt: this.attempt,
      retryInMs: this.retryInMs,
      slow,
      roundTripMs: this.scheduler.roundTripMs(),
    });
  }
}
