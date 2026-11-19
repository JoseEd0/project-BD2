import { describe, expect, it } from "vitest";

import { FrameScheduler } from "../src/realtime/scheduler";

const OPTIONS = { maxInFlight: 2, frameTimeoutMs: 1000, slowRoundTripMs: 300, roundTripSmoothing: 0.5 };

describe("frame scheduler", () => {
  it("never has more frames in flight than allowed", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    expect(scheduler.canSend(0)).toBe(true);
    expect(scheduler.send(0)).toBe(1);
    expect(scheduler.send(1)).toBe(2);
    expect(scheduler.canSend(2)).toBe(false);
    scheduler.answered(1, 50);
    expect(scheduler.canSend(51)).toBe(true);
  });

  it("waits the interval that the server's frame rate asks for", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.setMaxFps(10);
    scheduler.send(0);
    expect(scheduler.waitMs(40)).toBe(60);
    expect(scheduler.canSend(100)).toBe(true);
  });

  it("frees older frames when a newer one is answered, as the server drops them", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.send(0);
    scheduler.send(10);
    expect(scheduler.answered(2, 60)).toBe(50);
    expect(scheduler.inFlightCount()).toBe(0);
    expect(scheduler.answered(1, 70)).toBeNull();
  });

  it("gives up on a frame that is never answered", () => {
    const scheduler = new FrameScheduler({ ...OPTIONS, maxInFlight: 1 });
    scheduler.send(0);
    expect(scheduler.waitMs(400)).toBe(600);
    expect(scheduler.canSend(1000)).toBe(true);
  });

  it("smooths the round trip and flags a slow network", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.send(0);
    scheduler.answered(1, 100);
    expect(scheduler.roundTripMs()).toBe(100);
    expect(scheduler.isSlow(100)).toBe(false);
    scheduler.send(100);
    scheduler.answered(2, 900);
    expect(scheduler.roundTripMs()).toBe(450);
    expect(scheduler.isSlow(900)).toBe(true);
  });

  it("flags a slow network while a frame waits too long, before any answer", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.send(0);
    expect(scheduler.isSlow(200)).toBe(false);
    expect(scheduler.isSlow(400)).toBe(true);
  });

  it("frees a frame the server rejected, or all of them when it did not say which", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.send(0);
    scheduler.send(1);
    scheduler.failed(1);
    expect(scheduler.inFlightCount()).toBe(1);
    scheduler.failed(null);
    expect(scheduler.inFlightCount()).toBe(0);
  });

  it("starts over on a new connection but keeps counting frames", () => {
    const scheduler = new FrameScheduler(OPTIONS);
    scheduler.send(0);
    scheduler.answered(1, 500);
    scheduler.reset();
    expect(scheduler.roundTripMs()).toBeNull();
    expect(scheduler.canSend(0)).toBe(true);
    expect(scheduler.send(0)).toBe(2);
  });
});
