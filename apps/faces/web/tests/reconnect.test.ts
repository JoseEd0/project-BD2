import { describe, expect, it } from "vitest";

import { BUSY_BACKOFF, LOST_BACKOFF, backoffDelay, decideReconnect } from "../src/realtime/reconnect";

const middle = () => 0.5;

describe("reconnection", () => {
  it("stops after a close the app asked for", () => {
    expect(decideReconnect(1000, 0, middle)).toEqual({ kind: "stop" });
  });

  it("waits for the person after an idle close", () => {
    expect(decideReconnect(4408, 3, middle)).toEqual({ kind: "wait-for-interaction" });
  });

  it("backs off longer when the replica is full", () => {
    const busy = decideReconnect(1013, 0, middle);
    const lost = decideReconnect(1006, 0, middle);
    expect(busy).toEqual({ kind: "retry", reason: "busy", delayMs: BUSY_BACKOFF.baseMs });
    expect(lost).toEqual({ kind: "retry", reason: "lost", delayMs: LOST_BACKOFF.baseMs });
  });

  it("retries an internal error like a lost connection", () => {
    expect(decideReconnect(1011, 1, middle)).toEqual({ kind: "retry", reason: "lost", delayMs: LOST_BACKOFF.baseMs * 2 });
  });

  it("doubles the delay up to a ceiling, with jitter around it", () => {
    expect(backoffDelay(0, LOST_BACKOFF, middle)).toBe(500);
    expect(backoffDelay(3, LOST_BACKOFF, middle)).toBe(4000);
    expect(backoffDelay(20, LOST_BACKOFF, middle)).toBe(LOST_BACKOFF.maxMs);
    expect(backoffDelay(0, LOST_BACKOFF, () => 0)).toBe(350);
    expect(backoffDelay(0, LOST_BACKOFF, () => 1)).toBe(650);
  });
});
