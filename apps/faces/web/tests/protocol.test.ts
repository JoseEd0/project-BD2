import { describe, expect, it } from "vitest";

import {
  FRAME_HEADER_BYTES,
  MAX_FRAME_NUMBER,
  encodeFrame,
  helloMessage,
  nextFrameNumber,
  parseServerMessage,
} from "../src/realtime/protocol";

describe("frame encoding", () => {
  it("prefixes the image with the version and the big-endian frame number", () => {
    const message = encodeFrame(0x01020304, new Uint8Array([0xff, 0xd8, 0xff]));
    expect([...message]).toEqual([1, 1, 2, 3, 4, 0xff, 0xd8, 0xff]);
    expect(message.byteLength).toBe(FRAME_HEADER_BYTES + 3);
  });

  it("encodes the largest unsigned 32-bit frame number", () => {
    const message = encodeFrame(MAX_FRAME_NUMBER, new Uint8Array([7]));
    expect(new DataView(message.buffer).getUint32(1, false)).toBe(MAX_FRAME_NUMBER);
  });

  it("refuses frame numbers that do not fit in four bytes", () => {
    expect(() => encodeFrame(MAX_FRAME_NUMBER + 1, new Uint8Array([1]))).toThrow(RangeError);
    expect(() => encodeFrame(-1, new Uint8Array([1]))).toThrow(RangeError);
    expect(() => encodeFrame(1.5, new Uint8Array([1]))).toThrow(RangeError);
  });

  it("wraps the frame counter back to one", () => {
    expect(nextFrameNumber(0)).toBe(1);
    expect(nextFrameNumber(41)).toBe(42);
    expect(nextFrameNumber(MAX_FRAME_NUMBER)).toBe(1);
  });

  it("writes the hello that resumes a session", () => {
    expect(JSON.parse(helloMessage("ab".repeat(16)))).toEqual({ type: "hello", session: "ab".repeat(16), track_cache: true });
    expect(JSON.parse(helloMessage(null, false))).toEqual({ type: "hello", session: null, track_cache: false });
  });
});

describe("server messages", () => {
  it("reads ready with its limits", () => {
    const ready = parseServerMessage(
      JSON.stringify({
        type: "ready",
        session: "s".repeat(32),
        track_cache: true,
        limits: { max_frame_bytes: 1048576, max_frame_pixels: 2073600, max_fps: 15, idle_timeout_seconds: 30 },
      }),
    );
    expect(ready).toEqual({
      type: "ready",
      session: "s".repeat(32),
      trackCache: true,
      limits: { maxFrameBytes: 1048576, maxFramePixels: 2073600, maxFps: 15, idleTimeoutSeconds: 30 },
    });
  });

  it("reads a result with its faces and timings", () => {
    const result = parseServerMessage(
      JSON.stringify({
        type: "result",
        frame: 7,
        dropped: 2,
        faces: [
          { track: 1, box: { x: 10, y: 20, width: 100, height: 120 }, status: "known", label: "Ana", display_name: "Ana", score: 0.71, cached: true },
          { track: 2, box: { x: 300, y: 40, width: 50, height: 60 }, status: "unknown", label: "desconocido", display_name: null, score: 0.2, cached: false },
        ],
        timings: { wait: 0.1, decode: 1, detect: 6, track: 0.5, embed: 9, search: 2, total: 19 },
      }),
    );
    expect(result?.type).toBe("result");
    if (result?.type !== "result") {
      return;
    }
    expect(result.frame).toBe(7);
    expect(result.dropped).toBe(2);
    expect(result.faces.map((face) => [face.status, face.displayName, face.cached])).toEqual([
      ["known", "Ana", true],
      ["unknown", null, false],
    ]);
    expect(result.timings.total).toBe(19);
  });

  it("reads an error that names its frame", () => {
    expect(parseServerMessage(JSON.stringify({ type: "error", frame: 3, kind: "UnreadableImageError", error: "No se pudo leer" }))).toEqual({
      type: "error",
      frame: 3,
      kind: "UnreadableImageError",
      error: "No se pudo leer",
    });
  });

  it("ignores what is not a protocol message", () => {
    expect(parseServerMessage("not json")).toBeNull();
    expect(parseServerMessage("[]")).toBeNull();
    expect(parseServerMessage(JSON.stringify({ type: "other" }))).toBeNull();
    expect(parseServerMessage(JSON.stringify({ type: "result", frame: 1, faces: [{ status: "known" }] }))).toBeNull();
    expect(parseServerMessage(JSON.stringify({ type: "ready", session: "x", limits: { max_fps: 0 } }))).toBeNull();
  });
});
