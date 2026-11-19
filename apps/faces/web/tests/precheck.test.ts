import { describe, expect, it } from "vitest";

import { ShotGate, precheck } from "../src/enrollment/precheck";
import type { StreamFace } from "../src/realtime/protocol";

const FRAME = { width: 640, height: 480 };

function face(x: number, y: number, width: number): StreamFace {
  return { track: 1, box: { x, y, width, height: width * 1.2 }, status: "unknown", label: "desconocido", displayName: null, score: null, cached: false };
}

const centered = face(250, 130, 160);

describe("local pre-check", () => {
  it("accepts one big face in the oval", () => {
    expect(precheck([centered], FRAME)).toBeNull();
  });

  it("says what to fix", () => {
    expect(precheck([], FRAME)).toBe("no-face");
    expect(precheck([centered, face(20, 20, 150)], FRAME)).toBe("many-faces");
    expect(precheck([face(300, 180, 60)], FRAME)).toBe("too-far");
    expect(precheck([face(40, 0, 560)], FRAME)).toBe("too-close");
    expect(precheck([face(10, 130, 160)], FRAME)).toBe("off-center");
  });

  it("ignores someone small in the background, like the server", () => {
    expect(precheck([centered, face(20, 20, 50)], FRAME)).toBeNull();
  });
});

const GATE = { stableResults: 2, minIntervalMs: 1000, afterRejectionMs: 2000, rejectionBackoff: 2, maxAfterRejectionMs: 6000, blindIntervalMs: 3000, hintMaxAgeMs: 1500 };

describe("shot gate", () => {
  it("shoots after the face holds still in the oval", () => {
    const gate = new ShotGate(0, GATE);
    gate.observe(null, 0);
    expect(gate.shouldShoot(10)).toBe(false);
    gate.observe(null, 100);
    expect(gate.shouldShoot(110)).toBe(true);
  });

  it("starts counting again after a bad frame", () => {
    const gate = new ShotGate(0, GATE);
    gate.observe(null, 0);
    gate.observe("off-center", 100);
    gate.observe(null, 200);
    expect(gate.shouldShoot(210)).toBe(false);
  });

  it("sends one shot at a time and spaces them", () => {
    const gate = new ShotGate(0, GATE);
    gate.observe(null, 0);
    gate.observe(null, 100);
    gate.shot(100);
    gate.observe(null, 200);
    gate.observe(null, 300);
    expect(gate.shouldShoot(300)).toBe(false);
    gate.answered(true, 400);
    gate.observe(null, 1300);
    gate.observe(null, 1350);
    expect(gate.shouldShoot(1399)).toBe(false);
    expect(gate.shouldShoot(1400)).toBe(true);
  });

  it("gives time to read a rejection before the next shot", () => {
    const gate = new ShotGate(0, GATE);
    gate.shot(0);
    gate.answered(false, 100);
    gate.observe(null, 1500);
    gate.observe(null, 1600);
    expect(gate.shouldShoot(2000)).toBe(false);
    expect(gate.shouldShoot(2100)).toBe(true);
  });

  it("waits longer after each rejection in a row, up to a ceiling, and resets on success", () => {
    const gate = new ShotGate(0, { ...GATE, hintMaxAgeMs: 60_000 });
    const nextAllowed = (from: number) => {
      let at = from;
      while (!gate.shouldShoot(at)) {
        at += 100;
      }
      return at - from;
    };
    const reject = (at: number) => {
      gate.shot(at);
      gate.answered(false, at);
      gate.observe(null, at + 1);
      gate.observe(null, at + 2);
    };
    reject(10_000);
    expect(nextAllowed(10_000)).toBe(2000);
    reject(20_000);
    expect(nextAllowed(20_000)).toBe(4000);
    reject(30_000);
    expect(nextAllowed(30_000)).toBe(6000);
    gate.shot(40_000);
    gate.answered(true, 40_000);
    reject(50_000);
    expect(nextAllowed(50_000)).toBe(2000);
  });

  it("gives the socket time for its first box, then shoots blind and slowly", () => {
    const gate = new ShotGate(0, GATE);
    expect(gate.shouldShoot(1000)).toBe(false);
    expect(gate.shouldShoot(1600)).toBe(true);
    gate.shot(1600);
    gate.answered(false, 1600);
    expect(gate.shouldShoot(3600)).toBe(false);
    expect(gate.shouldShoot(5600)).toBe(true);
  });
});
