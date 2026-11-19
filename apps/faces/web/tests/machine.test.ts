import { describe, expect, it } from "vitest";

import type { ApiProblem } from "../src/api/problems";
import { INITIAL_STATE, enrollmentReducer, nameError, openEnrollmentId, type EnrollmentEvent, type EnrollmentState } from "../src/enrollment/machine";
import { ENROLLMENT_ID, PERSON, accepted, enrollment, rejected } from "./fixtures";

function run(...events: EnrollmentEvent[]): EnrollmentState {
  return events.reduce(enrollmentReducer, INITIAL_STATE);
}

function problem(kind: string, message = "mensaje", status = 409): ApiProblem {
  return { kind, message, status };
}

const capturing: EnrollmentEvent[] = [
  { type: "consent-accepted", version: "1-abc" },
  { type: "start-requested", name: "  Ana  " },
  { type: "started", enrollment: enrollment() },
];

describe("enrollment state machine", () => {
  it("asks for consent, then the name, trimmed", () => {
    expect(run({ type: "consent-accepted", version: "1-abc" })).toEqual({ step: "name", consentVersion: "1-abc", name: "", error: null });
    expect(run(...capturing.slice(0, 2))).toEqual({ step: "starting", consentVersion: "1-abc", name: "Ana" });
  });

  it("refuses an empty or too long name before calling the service", () => {
    expect(nameError("   ")).not.toBeNull();
    expect(nameError("x".repeat(61))).not.toBeNull();
    expect(nameError("Ana")).toBeNull();
    const state = run({ type: "consent-accepted", version: "v" }, { type: "start-requested", name: " " });
    expect(state).toMatchObject({ step: "name", error: expect.stringContaining("nombre") });
  });

  it("goes back to the name with the service's message", () => {
    const state = run(...capturing.slice(0, 2), { type: "start-failed", problem: problem("InvalidNameError", "Nombre inválido", 422) });
    expect(state).toEqual({ step: "name", consentVersion: "1-abc", name: "Ana", error: "Nombre inválido" });
  });

  it("goes back to the consent when its version changed", () => {
    const state = run(...capturing.slice(0, 2), { type: "start-failed", problem: problem("ConsentOutdatedError", "El texto cambió") });
    expect(state).toEqual({ step: "consent", notice: "El texto cambió" });
  });

  it("covers the poses one shot at a time and finishes on the last", () => {
    let state = run(...capturing, { type: "shot-sent" });
    expect(state).toMatchObject({ step: "capture", shooting: true });
    state = enrollmentReducer(state, { type: "shot-answered", shot: accepted(["front"]) });
    expect(state).toMatchObject({ step: "capture", shooting: false, accepted: 1, feedback: { tone: "accepted", pose: "front" } });
    state = enrollmentReducer(state, { type: "shot-answered", shot: accepted(["front", "left"]) });
    state = enrollmentReducer(state, { type: "shot-answered", shot: accepted(["front", "left", "right"]) });
    expect(state).toMatchObject({ step: "finishing", problem: null });
    expect(openEnrollmentId(state)).toBe(ENROLLMENT_ID);
  });

  it("shows the reason of a rejected shot and keeps going", () => {
    const state = run(...capturing, { type: "shot-sent" }, { type: "shot-answered", shot: rejected("blurry", "La foto salió borrosa.") });
    expect(state).toMatchObject({ step: "capture", shooting: false, accepted: 0, feedback: { tone: "rejected", reason: "blurry", message: "La foto salió borrosa." } });
  });

  it("blocks a duplicate or a lookalike with the service's message", () => {
    expect(run(...capturing, { type: "shot-failed", problem: problem("AlreadyEnrolledError", "Ya estás registrado como «Ana».") })).toEqual({
      step: "blocked",
      reason: "duplicate",
      message: "Ya estás registrado como «Ana».",
    });
    expect(run(...capturing, { type: "shot-failed", problem: problem("LookalikeError", "Te pareces mucho") })).toMatchObject({ reason: "lookalike" });
  });

  it("ends an expired session or one out of attempts", () => {
    expect(run(...capturing, { type: "shot-failed", problem: problem("EnrollmentNotFoundError", "venció", 404) })).toMatchObject({ step: "blocked", reason: "expired" });
    expect(run(...capturing, { type: "shot-failed", problem: problem("TooManyAttemptsError", "sin intentos", 429) })).toMatchObject({ step: "blocked", reason: "attempts" });
  });

  it("keeps capturing through a network failure or a shot already in progress", () => {
    expect(run(...capturing, { type: "shot-failed", problem: problem("NetworkError", "Sin red", 0) })).toMatchObject({ step: "capture", feedback: { tone: "trouble", message: "Sin red" } });
    expect(run(...capturing, { type: "shot-sent" }, { type: "shot-failed", problem: problem("ShotInProgressError") })).toMatchObject({ step: "capture", shooting: false, feedback: null });
  });

  it("retries a failed finish and blocks a duplicate found at the end", () => {
    const finishing = run(...capturing, { type: "shot-answered", shot: accepted(["front", "left", "right"]) });
    const failed = enrollmentReducer(finishing, { type: "finish-failed", problem: problem("NetworkError", "Sin red", 0) });
    expect(failed).toMatchObject({ step: "finishing", problem: { kind: "NetworkError" } });
    expect(enrollmentReducer(failed, { type: "finish-retried" })).toMatchObject({ step: "finishing", problem: null });
    expect(enrollmentReducer(finishing, { type: "finish-failed", problem: problem("AlreadyEnrolledError", "Ya estás") })).toMatchObject({ step: "blocked", reason: "duplicate" });
  });

  it("ends with the person and closes the session", () => {
    const finishing = run(...capturing, { type: "shot-answered", shot: accepted(["front", "left", "right"]) });
    const done = enrollmentReducer(finishing, { type: "finished", enrolled: { person: PERSON, message: "Listo" } });
    expect(done).toEqual({ step: "done", person: PERSON, message: "Listo" });
    expect(openEnrollmentId(done)).toBeNull();
  });

  it("ignores events that do not belong to the current step", () => {
    const consent = run({ type: "shot-sent" }, { type: "finished", enrolled: { person: PERSON, message: "" } });
    expect(consent).toEqual(INITIAL_STATE);
    expect(run(...capturing, { type: "restart" })).toEqual(INITIAL_STATE);
  });
});
