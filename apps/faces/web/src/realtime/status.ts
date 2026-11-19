import type { StreamState } from "./stream";

export type StatusTone = "live" | "slow" | "waiting" | "off" | "paused";

export interface StreamStatusView {
  readonly tone: StatusTone;
  readonly label: string;
}

const MILLISECONDS = new Intl.NumberFormat("es", { maximumFractionDigits: 0 });

function seconds(ms: number | null): string {
  return `${Math.max(1, Math.ceil((ms ?? 0) / 1000))} s`;
}

/** Lo que dice la píldora de estado sobre la cámara. */
export function streamStatus(state: StreamState): StreamStatusView {
  switch (state.phase) {
    case "live": {
      const latency = state.roundTripMs === null ? "" : ` · ${MILLISECONDS.format(state.roundTripMs)} ms`;
      return state.slow ? { tone: "slow", label: `Red lenta${latency}` } : { tone: "live", label: `En vivo${latency}` };
    }
    case "connecting":
      return { tone: "waiting", label: "Conectando…" };
    case "retrying":
      return { tone: "off", label: `Sin conexión · reintento en ${seconds(state.retryInMs)}` };
    case "busy":
      return { tone: "waiting", label: `Servicio lleno · reintento en ${seconds(state.retryInMs)}` };
    case "idle":
      return { tone: "paused", label: "En pausa · toca para seguir" };
    case "stopped":
      return { tone: "paused", label: "En pausa" };
  }
}
