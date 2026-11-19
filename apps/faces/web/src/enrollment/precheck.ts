import type { Size } from "../camera/geometry";
import type { StreamFace } from "../realtime/protocol";

/**
 * Lo que el celular revisa antes de gastar una toma, con la caja que devuelve el socket. Es
 * más laxo que el servidor: solo evita mandar fotos que de seguro se rechazan.
 */
export type PrecheckIssue = "no-face" | "many-faces" | "too-far" | "too-close" | "off-center";

export interface PrecheckOptions {
  /**
   * Ancho mínimo y máximo de la cara, como fracción del lado **menor** del cuadro: así vale
   * igual con la cámara apaisada (escritorio) que vertical (celular). Con 0,18 la cara de una
   * toma de 1 280 x 720 mide unos 130 px, sobre los 80 que exige el servidor.
   */
  readonly minWidthFraction: number;
  readonly maxWidthFraction: number;
  /** Cuánto se puede alejar el centro de la cara del centro del óvalo, en fracción del cuadro. */
  readonly centerTolerance: number;
  /** Dónde está el centro del óvalo, en fracción del alto del cuadro. */
  readonly guideCenterY: number;
  /** Un rostro de menos de esta fracción del ancho del mayor es alguien al fondo (como el servidor). */
  readonly backgroundFaceRatio: number;
}

export const PRECHECK: PrecheckOptions = {
  minWidthFraction: 0.18,
  maxWidthFraction: 0.85,
  centerTolerance: 0.2,
  guideCenterY: 0.4,
  backgroundFaceRatio: 0.5,
};

export const PRECHECK_MESSAGES: Record<PrecheckIssue, string> = {
  "no-face": "Pon tu cara dentro del óvalo.",
  "many-faces": "Que solo se vea tu cara: pide a los demás que se aparten.",
  "too-far": "Acércate un poco.",
  "too-close": "Aléjate un poco.",
  "off-center": "Centra tu cara en el óvalo.",
};

export function precheck(faces: readonly StreamFace[], frame: Size, options: PrecheckOptions = PRECHECK): PrecheckIssue | null {
  const [largest] = [...faces].sort((a, b) => b.box.width - a.box.width);
  if (largest === undefined) {
    return "no-face";
  }
  const peers = faces.filter((face) => face.box.width >= largest.box.width * options.backgroundFaceRatio);
  if (peers.length > 1) {
    return "many-faces";
  }
  const widthFraction = largest.box.width / Math.min(frame.width, frame.height);
  if (widthFraction < options.minWidthFraction) {
    return "too-far";
  }
  if (widthFraction > options.maxWidthFraction) {
    return "too-close";
  }
  const centerX = (largest.box.x + largest.box.width / 2) / frame.width;
  const centerY = (largest.box.y + largest.box.height / 2) / frame.height;
  if (Math.abs(centerX - 0.5) > options.centerTolerance || Math.abs(centerY - options.guideCenterY) > options.centerTolerance) {
    return "off-center";
  }
  return null;
}

export interface ShotGateOptions {
  /** Respuestas seguidas sin problemas antes de disparar: la cara está quieta en el óvalo. */
  readonly stableResults: number;
  /** Espera mínima entre dos tomas (el nginx admite pocas peticiones por segundo por IP). */
  readonly minIntervalMs: number;
  /** Tras un rechazo, tiempo para leer el mensaje y corregir antes de la siguiente. */
  readonly afterRejectionMs: number;
  /**
   * Cada rechazo seguido multiplica esa espera, hasta `maxAfterRejectionMs`: la sesión tiene
   * pocos intentos, y quien no logra girar la cabeza no debe gastarlos en un minuto.
   */
  readonly rejectionBackoff: number;
  readonly maxAfterRejectionMs: number;
  /** Sin respuestas del socket (caído o lento), se dispara cada tanto igual. */
  readonly blindIntervalMs: number;
  /** Cuánto vale la última respuesta del socket antes de considerarlo mudo. */
  readonly hintMaxAgeMs: number;
}

export const SHOT_GATE: ShotGateOptions = {
  stableResults: 2,
  minIntervalMs: 1_200,
  afterRejectionMs: 2_200,
  rejectionBackoff: 1.5,
  maxAfterRejectionMs: 8_000,
  blindIntervalMs: 3_000,
  hintMaxAgeMs: 2_000,
};

/**
 * Cuándo sale la siguiente toma: una a la vez, con la cara bien puesta en las últimas
 * respuestas del socket, y nunca más seguido que `minIntervalMs`. Si el socket no responde,
 * dispara a ciegas cada `blindIntervalMs` y deja que el servidor juzgue.
 */
export class ShotGate {
  private stable = 0;
  private lastHintAt: number;
  private nextShotAt = Number.NEGATIVE_INFINITY;
  private inFlight = false;
  private rejectionsInARow = 0;

  /** `now` es cuando empieza a mirar: el socket tiene `hintMaxAgeMs` para dar su primera caja. */
  constructor(now: number, private readonly options: ShotGateOptions = SHOT_GATE) {
    this.lastHintAt = now;
  }

  /** Una respuesta del socket con su veredicto local. */
  observe(issue: PrecheckIssue | null, now: number): void {
    this.lastHintAt = now;
    this.stable = issue === null ? this.stable + 1 : 0;
  }

  shouldShoot(now: number): boolean {
    if (this.inFlight || now < this.nextShotAt) {
      return false;
    }
    if (now - this.lastHintAt > this.options.hintMaxAgeMs) {
      return now >= this.nextShotAt + this.options.blindIntervalMs - this.options.minIntervalMs;
    }
    return this.stable >= this.options.stableResults;
  }

  shot(now: number): void {
    this.inFlight = true;
    this.nextShotAt = now + this.options.minIntervalMs;
  }

  answered(accepted: boolean, now: number): void {
    this.inFlight = false;
    this.stable = 0;
    this.rejectionsInARow = accepted ? 0 : this.rejectionsInARow + 1;
    this.nextShotAt = now + (accepted ? this.options.minIntervalMs : this.rejectionWaitMs());
  }

  private rejectionWaitMs(): number {
    const grown = this.options.afterRejectionMs * this.options.rejectionBackoff ** (this.rejectionsInARow - 1);
    return Math.min(grown, this.options.maxAfterRejectionMs);
  }
}
