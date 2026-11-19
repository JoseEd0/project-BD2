import { useEffect, useMemo, useState } from "react";

import { layoutSpans } from "../analysis/spans";
import { useServiceHealth } from "../api/useServiceHealth";
import { Icon } from "../components/Icon";
import { UNAVAILABLE_REASONS } from "../components/ServiceBadge";
import { APP } from "../identity";
import { AbPanel } from "../listening/AbPanel";
import { useAbPlayer } from "../listening/useAbPlayer";
import { DropZone } from "../studio/DropZone";
import { FileBar } from "../studio/FileBar";
import { MatchTable } from "../studio/MatchTable";
import { ProblemPanel } from "../studio/ProblemPanel";
import { Progress } from "../studio/Progress";
import { StageTimings } from "../studio/StageTimings";
import { useStudio, type Phase, type WaveformState } from "../studio/useStudio";
import { VerdictLine } from "../studio/VerdictLine";
import { Timeline } from "../timeline/Timeline";

function knownDuration(phase: Phase, waveform: WaveformState): number | null {
  if (phase.kind === "done") {
    return phase.result.detection.duration_seconds;
  }
  return waveform.status === "ready" ? waveform.waveform.durationSeconds : null;
}

function ServiceDown({ reason, onRetry, retrying }: { readonly reason: string; readonly onRetry: () => void; readonly retrying: boolean }) {
  return (
    <div className="service-down" role="alert">
      <Icon name="alert" />
      <p>
        <strong>El servicio de detección no responde:</strong> {reason}. Podrás subir el audio cuando vuelva.
      </p>
      <button type="button" className="button button--quiet" onClick={onRetry} disabled={retrying}>
        <Icon name="retry" />
        Volver a comprobar
      </button>
    </div>
  );
}

export default function StudioPage() {
  const studio = useStudio();
  const { phase, waveform, file } = studio;
  const health = useServiceHealth();
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const detection = phase.kind === "done" ? phase.result.detection : null;
  const duration = knownDuration(phase, waveform);
  const layout = useMemo(
    () => (detection === null || duration === null ? null : layoutSpans(detection.matches, duration)),
    [detection, duration],
  );
  const selected = selectedIndex === null ? null : (layout?.spans[selectedIndex] ?? null);
  const player = useAbPlayer(selected?.match ?? null);

  useEffect(() => {
    setSelectedIndex(layout !== null && layout.spans.length > 0 ? 0 : null);
  }, [layout]);

  useEffect(() => {
    if (detection !== null) {
      document.getElementById("verdict-title")?.focus();
    }
  }, [detection]);

  const unavailable = health.data?.state === "unavailable" ? health.data : null;
  const busy = phase.kind === "uploading" || phase.kind === "analyzing";

  if (file === null) {
    return (
      <>
        <title>{`${APP.name} · ${APP.tagline}`}</title>
        <DropZone onFile={studio.analyse} disabled={unavailable !== null} notice={phase.kind === "idle" ? phase.notice : null}>
          {unavailable !== null && (
            <ServiceDown reason={UNAVAILABLE_REASONS[unavailable.reason]} onRetry={() => void health.refetch()} retrying={health.isFetching} />
          )}
        </DropZone>
      </>
    );
  }

  return (
    <>
      <title>{`${file.name} · ${APP.name}`}</title>
      <h1 className="visually-hidden">Análisis de «{file.name}»</h1>
      <FileBar file={file} durationSeconds={duration} busy={busy} onFile={studio.analyse} onCancel={studio.cancel} />
      <Progress phase={phase} />
      {phase.kind === "problem" && <ProblemPanel problem={phase.problem} onRetry={studio.retry} onChooseAnother={studio.reset} />}
      {detection !== null && <VerdictLine detection={detection} />}
      {duration === null ? (
        <div className="score score--pending" aria-hidden="true" />
      ) : (
        <Timeline
          durationSeconds={duration}
          hopSeconds={detection?.hop_seconds ?? 0}
          peaks={waveform.status === "ready" ? waveform.waveform.peaks : null}
          layout={layout}
          selected={selected}
          onSelect={setSelectedIndex}
          analyzing={busy}
          uploadPlayhead={selected === null ? null : player.uploadTime}
          originalPlayhead={selected === null ? null : player.originalTime}
        />
      )}
      {waveform.status === "unavailable" && <p className="quiet-note">Este navegador no pudo dibujar la forma de onda de este archivo; el análisis no cambia.</p>}
      {detection !== null && layout !== null && layout.spans.length > 0 && (
        <div className="findings">
          <MatchTable spans={layout.spans} selected={selectedIndex} threshold={detection.threshold} onSelect={setSelectedIndex} />
          {selected !== null && studio.audioUrl !== null && <AbPanel span={selected} uploadUrl={studio.audioUrl} player={player} />}
        </div>
      )}
      {phase.kind === "done" && <StageTimings serverTiming={phase.result.serverTiming} engineTimings={phase.result.detection.engine_timings} />}
    </>
  );
}
