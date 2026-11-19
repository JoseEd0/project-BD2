import type { Enrolled, Enrollment, Person, Pose, ShotResult } from "../api/faces";
import type { ApiProblem } from "../api/problems";

/**
 * El registro como máquina de estados pura: la pantalla despacha lo que pasó (respuestas del
 * API, decisiones de la persona) y dibuja el estado.
 *
 * consent → name → starting → capture ⇄ (toma) → finishing → done
 *                                  ↘ blocked (duplicado, parecido, venció, sin intentos)
 */
export type BlockReason = "duplicate" | "lookalike" | "expired" | "attempts";

export type ShotFeedback =
  | { readonly tone: "accepted"; readonly pose: Pose; readonly message: string }
  | { readonly tone: "rejected"; readonly reason: string | null; readonly message: string }
  | { readonly tone: "trouble"; readonly message: string };

export type EnrollmentState =
  | { readonly step: "consent"; readonly notice: string | null }
  | { readonly step: "name"; readonly consentVersion: string; readonly name: string; readonly error: string | null }
  | { readonly step: "starting"; readonly consentVersion: string; readonly name: string }
  | {
      readonly step: "capture";
      readonly enrollment: Enrollment;
      readonly shooting: boolean;
      readonly feedback: ShotFeedback | null;
      /** Sube con cada toma aceptada: la pantalla destella una vez por cada una. */
      readonly accepted: number;
    }
  | { readonly step: "finishing"; readonly enrollment: Enrollment; readonly problem: ApiProblem | null }
  | { readonly step: "done"; readonly person: Person; readonly message: string }
  | { readonly step: "blocked"; readonly reason: BlockReason; readonly message: string };

export type EnrollmentEvent =
  | { readonly type: "consent-accepted"; readonly version: string }
  | { readonly type: "start-requested"; readonly name: string }
  | { readonly type: "started"; readonly enrollment: Enrollment }
  | { readonly type: "start-failed"; readonly problem: ApiProblem }
  | { readonly type: "shot-sent" }
  | { readonly type: "shot-answered"; readonly shot: ShotResult }
  | { readonly type: "shot-failed"; readonly problem: ApiProblem }
  | { readonly type: "finish-failed"; readonly problem: ApiProblem }
  | { readonly type: "finish-retried" }
  | { readonly type: "finished"; readonly enrolled: Enrolled }
  | { readonly type: "restart" };

export const INITIAL_STATE: EnrollmentState = { step: "consent", notice: null };

const BLOCK_BY_KIND: Record<string, BlockReason> = {
  AlreadyEnrolledError: "duplicate",
  LookalikeError: "lookalike",
  EnrollmentNotFoundError: "expired",
  TooManyAttemptsError: "attempts",
};

const CONSENT_OUTDATED = "ConsentOutdatedError";
const CONSENT_REQUIRED = "ConsentRequiredError";
const SHOT_IN_PROGRESS = "ShotInProgressError";

export const NAME_MAX_LENGTH = 60;

/** Lo que el servidor rechazaría seguro, dicho antes de enviarlo. */
export function nameError(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed.length === 0) {
    return "Escribe tu nombre: es lo que verá la cámara al reconocerte.";
  }
  if (trimmed.length > NAME_MAX_LENGTH) {
    return `Usa como máximo ${NAME_MAX_LENGTH} caracteres.`;
  }
  return null;
}

function blocked(problem: ApiProblem): EnrollmentState | null {
  const reason = BLOCK_BY_KIND[problem.kind];
  return reason === undefined ? null : { step: "blocked", reason, message: problem.message };
}

function consentAgain(problem: ApiProblem): EnrollmentState | null {
  if (problem.kind === CONSENT_OUTDATED || problem.kind === CONSENT_REQUIRED) {
    return { step: "consent", notice: problem.message };
  }
  return null;
}

function afterStartFailed(state: EnrollmentState, problem: ApiProblem): EnrollmentState {
  if (state.step !== "starting") {
    return state;
  }
  const back = consentAgain(problem);
  if (back !== null) {
    return back;
  }
  return { step: "name", consentVersion: state.consentVersion, name: state.name, error: problem.message };
}

function afterShot(state: EnrollmentState, shot: ShotResult): EnrollmentState {
  if (state.step !== "capture") {
    return state;
  }
  if (!shot.accepted) {
    return { ...state, enrollment: shot.enrollment, shooting: false, feedback: { tone: "rejected", reason: shot.reason, message: shot.message } };
  }
  if (shot.enrollment.remaining.length === 0) {
    return { step: "finishing", enrollment: shot.enrollment, problem: null };
  }
  return {
    ...state,
    enrollment: shot.enrollment,
    shooting: false,
    accepted: state.accepted + 1,
    feedback: { tone: "accepted", pose: shot.pose ?? "front", message: shot.enrollment.message },
  };
}

function afterShotFailed(state: EnrollmentState, problem: ApiProblem): EnrollmentState {
  if (state.step !== "capture") {
    return state;
  }
  const block = blocked(problem);
  if (block !== null) {
    return block;
  }
  if (problem.kind === SHOT_IN_PROGRESS) {
    return { ...state, shooting: false };
  }
  return { ...state, shooting: false, feedback: { tone: "trouble", message: problem.message } };
}

export function enrollmentReducer(state: EnrollmentState, event: EnrollmentEvent): EnrollmentState {
  switch (event.type) {
    case "consent-accepted":
      return state.step === "consent" ? { step: "name", consentVersion: event.version, name: "", error: null } : state;
    case "start-requested": {
      if (state.step !== "name") {
        return state;
      }
      const error = nameError(event.name);
      return error === null
        ? { step: "starting", consentVersion: state.consentVersion, name: event.name.trim() }
        : { ...state, name: event.name, error };
    }
    case "started":
      return state.step === "starting" ? { step: "capture", enrollment: event.enrollment, shooting: false, feedback: null, accepted: 0 } : state;
    case "start-failed":
      return afterStartFailed(state, event.problem);
    case "shot-sent":
      return state.step === "capture" ? { ...state, shooting: true } : state;
    case "shot-answered":
      return afterShot(state, event.shot);
    case "shot-failed":
      return afterShotFailed(state, event.problem);
    case "finish-failed":
      if (state.step !== "finishing") {
        return state;
      }
      return blocked(event.problem) ?? { ...state, problem: event.problem };
    case "finish-retried":
      return state.step === "finishing" ? { ...state, problem: null } : state;
    case "finished":
      return state.step === "finishing" ? { step: "done", person: event.enrolled.person, message: event.enrolled.message } : state;
    case "restart":
      return INITIAL_STATE;
  }
}

/** La sesión del servidor que sigue abierta en este estado, para cancelarla al salir. */
export function openEnrollmentId(state: EnrollmentState): string | null {
  return state.step === "capture" || state.step === "finishing" ? state.enrollment.enrollment_id : null;
}
