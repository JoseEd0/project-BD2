import { describe, expect, it } from "vitest";

import { streamStatus } from "../src/realtime/status";
import type { StreamState } from "../src/realtime/stream";

const BASE: StreamState = { phase: "live", attempt: 0, retryInMs: null, slow: false, roundTripMs: 42.4 };

describe("stream status", () => {
  it("shows the latency while live and warns about a slow network", () => {
    expect(streamStatus(BASE)).toEqual({ tone: "live", label: "En vivo · 42 ms" });
    expect(streamStatus({ ...BASE, slow: true, roundTripMs: 910 })).toEqual({ tone: "slow", label: "Red lenta · 910 ms" });
  });

  it("says when it reconnects, when the service is full and when it is paused", () => {
    expect(streamStatus({ ...BASE, phase: "retrying", attempt: 1, retryInMs: 1500 }).label).toBe("Sin conexión · reintento en 2 s");
    expect(streamStatus({ ...BASE, phase: "busy", retryInMs: 2000 })).toEqual({ tone: "waiting", label: "Servicio lleno · reintento en 2 s" });
    expect(streamStatus({ ...BASE, phase: "idle" }).label).toBe("En pausa · toca para seguir");
    expect(streamStatus({ ...BASE, phase: "connecting" }).label).toBe("Conectando…");
  });
});
