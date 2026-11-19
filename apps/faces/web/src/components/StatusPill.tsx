import { streamStatus } from "../realtime/status";
import type { StreamState } from "../realtime/stream";

export function StatusPill({ state }: { readonly state: StreamState }) {
  const { tone, label } = streamStatus(state);
  return (
    <span className="status-pill tabular" data-tone={tone}>
      <span className="status-dot" aria-hidden="true" />
      {label}
    </span>
  );
}
