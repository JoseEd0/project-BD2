import { describe, expect, it } from "vitest";

import { NO_FILTERS } from "../src/api/types";
import { failureOfEvent } from "../src/research/failures";
import { turnsReducer, type Turn, type TurnAction } from "../src/research/turns";
import { done, SOURCES } from "./api";

const ID = "t1";

function run(actions: readonly TurnAction[]): Turn {
  const turns = actions.reduce(turnsReducer, turnsReducer([], { type: "asked", id: ID, question: "q", filters: NO_FILTERS, at: 1000 }));
  const turn = turns[0];
  if (turn === undefined) {
    throw new Error("sin turno");
  }
  return turn;
}

const SOURCES_EVENT: TurnAction = { type: "event", id: ID, at: 1100, event: { type: "sources", data: { question: "q", model: "m", sources: SOURCES } } };

function delta(text: string, at: number): TurnAction {
  return { type: "event", id: ID, at, event: { type: "delta", text } };
}

describe("turnsReducer", () => {
  it("starts retrieving, streams after the sources and settles on done", () => {
    expect(run([]).status).toBe("retrieving");
    const streaming = run([SOURCES_EVENT, delta("Hola ", 1250), delta("[1].", 1300)]);
    expect(streaming).toMatchObject({ status: "streaming", text: "Hola [1].", sources: SOURCES, firstTextMs: 250, totalMs: null });
    const answered = run([SOURCES_EVENT, delta("Hola ", 1250), { type: "event", id: ID, at: 1900, event: { type: "done", data: done("Hola [1].") } }]);
    expect(answered).toMatchObject({ status: "answered", text: "Hola [1].", totalMs: 900, model: "claude-sonnet-5-5" });
  });

  it("keeps the server timing from the headers", () => {
    expect(run([{ type: "opened", id: ID, serverTiming: "bm25;dur=3" }]).serverTiming).toBe("bm25;dur=3");
  });

  it("fails with the error event's kind and keeps the partial text", () => {
    const turn = run([SOURCES_EVENT, delta("Parcial", 1200), { type: "event", id: ID, at: 1500, event: { type: "error", data: { error: "caído", kind: "LlmUnavailableError", request_id: "r" } } }]);
    expect(turn.status).toBe("failed");
    expect(turn.text).toBe("Parcial");
    expect(turn.failure).toEqual(failureOfEvent("LlmUnavailableError", "caído", "r"));
    expect(turn.failure?.kind).toBe("model-unavailable");
  });

  it("calls a body that closes without done an interrupted answer", () => {
    const turn = run([SOURCES_EVENT, delta("Medio", 1200), { type: "ended", id: ID, at: 1300 }]);
    expect(turn.status).toBe("failed");
    expect(turn.failure?.kind).toBe("interrupted");
  });

  it("ignores what arrives after the turn settled", () => {
    const stopped = run([SOURCES_EVENT, { type: "stopped", id: ID, at: 1200 }, delta("tarde", 1300), { type: "ended", id: ID, at: 1400 }]);
    expect(stopped).toMatchObject({ status: "stopped", text: "", totalMs: 200, failure: null });
    const answered = run([{ type: "event", id: ID, at: 1500, event: { type: "done", data: done("") } }, { type: "ended", id: ID, at: 1600 }]);
    expect(answered.status).toBe("answered");
  });

  it("records the model the API fell back to", () => {
    const turn = run([SOURCES_EVENT, { type: "event", id: ID, at: 1200, event: { type: "fallback", data: { from_model: "a", to_model: "b" } } }]);
    expect(turn.fallback).toEqual({ from_model: "a", to_model: "b" });
    expect(turn.model).toBe("b");
  });

  it("only touches the turn the action names", () => {
    const first = turnsReducer([], { type: "asked", id: "a", question: "uno", filters: NO_FILTERS, at: 0 });
    const both = turnsReducer(first, { type: "asked", id: "b", question: "dos", filters: NO_FILTERS, at: 10 });
    const after = turnsReducer(both, { type: "stopped", id: "b", at: 20 });
    expect(after.map((turn) => turn.status)).toEqual(["retrieving", "stopped"]);
  });
});
