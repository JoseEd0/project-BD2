import { describe, expect, it } from "vitest";

import { RecognitionStream, type Clock, type FrameAnswer, type StreamState } from "../src/realtime/stream";

class FakeSocket {
  binaryType = "blob";
  readyState = 0;
  bufferedAmount = 0;
  readonly sent: (string | Uint8Array)[] = [];
  closedWith: number | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(readonly url: string) {}

  send(data: string | Uint8Array): void {
    this.sent.push(data);
  }

  close(code: number): void {
    this.closedWith = code;
    this.readyState = 3;
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }

  frames(): Uint8Array[] {
    return this.sent.filter((item): item is Uint8Array => item instanceof Uint8Array);
  }

  texts(): unknown[] {
    return this.sent.filter((item): item is string => typeof item === "string").map((text) => JSON.parse(text) as unknown);
  }
}

class ManualClock implements Clock {
  time = 0;
  private nextHandle = 1;
  private readonly timers = new Map<number, { at: number; callback: () => void }>();

  now(): number {
    return this.time;
  }

  setTimeout(callback: () => void, ms: number): number {
    const handle = this.nextHandle++;
    this.timers.set(handle, { at: this.time + ms, callback });
    return handle;
  }

  clearTimeout(handle: number): void {
    this.timers.delete(handle);
  }

  advance(ms: number): void {
    this.time += ms;
    for (const [handle, timer] of [...this.timers]) {
      if (timer.at <= this.time) {
        this.timers.delete(handle);
        timer.callback();
      }
    }
  }
}

const SESSION = "a".repeat(32);
const SIZE = { width: 640, height: 480 };

function ready(maxFps = 1000) {
  return { type: "ready", session: SESSION, track_cache: true, limits: { max_frame_bytes: 1 << 20, max_frame_pixels: 2073600, max_fps: maxFps, idle_timeout_seconds: 30 } };
}

function result(frame: number) {
  return {
    type: "result",
    frame,
    dropped: 0,
    faces: [{ track: 1, box: { x: 1, y: 2, width: 3, height: 4 }, status: "known", label: "Ana", display_name: "Ana", score: 0.8, cached: false }],
    timings: { wait: 0, decode: 1, detect: 2, track: 0, embed: 3, search: 1, total: 7 },
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function harness(maxInFlight = 1) {
  const sockets: FakeSocket[] = [];
  const states: StreamState[] = [];
  const answers: FrameAnswer[] = [];
  const clock = new ManualClock();
  let captures = 0;
  const stream = new RecognitionStream({
    url: "wss://rostros.test/api/ws/recognize",
    scheduler: { maxInFlight, frameTimeoutMs: 5000, slowRoundTripMs: 700, roundTripSmoothing: 0.5 },
    capture: () => {
      captures += 1;
      return Promise.resolve({ bytes: new Uint8Array([0xff, 0xd8]), size: SIZE });
    },
    onState: (state) => states.push(state),
    onAnswer: (answer) => answers.push(answer),
    createSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    clock,
    random: () => 0.5,
  });
  return { stream, sockets, states, answers, clock, captures: () => captures, last: () => states[states.length - 1] };
}

describe("recognition stream", () => {
  it("sends frames only after ready and one at a time", async () => {
    const { stream, sockets, answers, clock } = harness();
    stream.start();
    const socket = sockets[0]!;
    socket.open();
    await settle();
    expect(socket.frames()).toHaveLength(0);
    socket.receive(ready());
    await settle();
    expect(socket.frames()).toHaveLength(1);
    clock.advance(30);
    await settle();
    expect(socket.frames()).toHaveLength(1);
    socket.receive(result(1));
    await settle();
    expect(answers[0]?.roundTripMs).toBe(30);
    expect(answers[0]?.size).toEqual(SIZE);
    expect(socket.frames()).toHaveLength(2);
    expect(new DataView(socket.frames()[1]!.buffer).getUint32(1, false)).toBe(2);
  });

  it("does not capture while the socket still has bytes to send", async () => {
    const { stream, sockets, captures } = harness(2);
    stream.start();
    const socket = sockets[0]!;
    socket.open();
    socket.bufferedAmount = 4096;
    socket.receive(ready());
    await settle();
    expect(captures()).toBe(0);
  });

  it("reconnects after a lost connection and resumes its session with a hello", async () => {
    const { stream, sockets, clock, last } = harness();
    stream.start();
    sockets[0]!.open();
    sockets[0]!.receive(ready());
    await settle();
    sockets[0]!.drop(1006);
    expect(last()).toMatchObject({ phase: "retrying", attempt: 1, retryInMs: 500 });
    clock.advance(500);
    expect(sockets).toHaveLength(2);
    sockets[1]!.open();
    expect(sockets[1]!.texts()).toEqual([{ type: "hello", session: SESSION, track_cache: true }]);
    sockets[1]!.receive(ready());
    expect(last()).toMatchObject({ phase: "live", attempt: 0 });
  });

  it("waits longer when the replica is full", () => {
    const { stream, sockets, clock, last } = harness();
    stream.start();
    sockets[0]!.open();
    sockets[0]!.drop(1013);
    expect(last()).toMatchObject({ phase: "busy", retryInMs: 2000 });
    clock.advance(1999);
    expect(sockets).toHaveLength(1);
    clock.advance(1);
    expect(sockets).toHaveLength(2);
  });

  it("stays idle after an idle close until it is resumed", () => {
    const { stream, sockets, clock, last } = harness();
    stream.start();
    sockets[0]!.open();
    sockets[0]!.receive(ready());
    sockets[0]!.drop(4408);
    expect(last()?.phase).toBe("idle");
    clock.advance(60_000);
    expect(sockets).toHaveLength(1);
    stream.resume();
    expect(sockets).toHaveLength(2);
  });

  it("closes cleanly when stopped and never reconnects", () => {
    const { stream, sockets, clock, last } = harness();
    stream.start();
    sockets[0]!.open();
    stream.stop();
    expect(sockets[0]!.closedWith).toBe(1000);
    expect(last()?.phase).toBe("stopped");
    clock.advance(60_000);
    expect(sockets).toHaveLength(1);
  });

  it("frees the slot of a frame the server rejected", async () => {
    const { stream, sockets, clock } = harness();
    stream.start();
    const socket = sockets[0]!;
    socket.open();
    socket.receive(ready());
    await settle();
    socket.receive({ type: "error", frame: 1, kind: "UnreadableImageError", error: "x" });
    clock.advance(1);
    await settle();
    expect(socket.frames()).toHaveLength(2);
  });
});
