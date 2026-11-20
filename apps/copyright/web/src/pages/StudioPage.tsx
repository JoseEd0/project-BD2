import { useEffect, useMemo, useState } from "react";

import { notableMatches, withoutSong } from "../analysis/catalog";
import { layoutSpans } from "../analysis/spans";
import { CatalogPanel } from "../catalog/CatalogPanel";
import { DemoClipsPanel } from "../catalog/DemoClipsPanel";
import { songAudioUrl } from "../api/client";
import { useServiceHealth } from "../api/useServiceHealth";
import { Icon } from "../components/Icon";
import { UNAVAILABLE_REASONS } from "../components/ServiceBadge";
import { APP } from "../identity";
import { useMatchPlayback } from "../listening/useMatchPlayback";
import { QueryPlayer } from "../player/QueryPlayer";
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
  const { phase, waveform, file, source } = studio;
  const health = useServiceHealth();
  const [showWeak, setShowWeak] = useState(false);

  const detection = useMemo(() => {
    if (phase.kind !== "done") {
      return null;
    }
    return source === null ? phase.result.detection : withoutSong(phase.result.detection, source.id);
  }, [phase, source]);
  const duration = knownDuration(phase, waveform);
  const layout = useMemo(
    () => (detection === null || duration === null ? null : layoutSpans(showWeak ? detection.matches : notableMatches(detection.matches), duration)),
    [detection, duration, showWeak],
  );
  const hiddenWeak = detection === null || layout === null ? 0 : detection.matches.length - layout.spans.length;
  const { selected, select, player, playing, play } = useMatchPlayback(layout);

  useEffect(() => {
    if (detection !== null) {
      document.getElementById("verdict-title")?.focus();
    }
  }, [detection]);

  const unavailable = health.data?.state === "unavailable" ? health.data : null;
  const busy = phase.kind === "uploading" || phase.kind === "analyzing";

  const catalog = (
    <div className="side">
      <DemoClipsPanel onAnalyse={studio.analyseClip} />
      <CatalogPanel chosenId={source?.id ?? null} onFindSimilar={studio.analyseSong} />
    </div>
  );

  if (file === null) {
    return (
      <div className="studio-grid">
        <title>{`${APP.name} · ${APP.tagline}`}</title>
        {catalog}
        <div className="bench">
          {source !== null ? (
            <>
              <h1 className="bench__title">Parecidas a «{source.title}»</h1>
              <Progress phase={phase} />
              {phase.kind === "problem" && <ProblemPanel problem={phase.problem} onRetry={studio.retry} onChooseAnother={studio.reset} />}
            </>
          ) : (
            <DropZone onFile={studio.analyse} disabled={unavailable !== null} notice={phase.kind === "idle" ? phase.notice : null}>
              {unavailable !== null && (
                <ServiceDown reason={UNAVAILABLE_REASONS[unavailable.reason]} onRetry={() => void health.refetch()} retrying={health.isFetching} />
              )}
            </DropZone>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="studio-grid">
      <title>{`${file.name} · ${APP.name}`}</title>
      {catalog}
      <div className="bench">
        <h1 className={source === null ? "visually-hidden" : "bench__title"}>
          {source === null ? `Análisis de «${file.name}»` : `Parecidas a «${source.title}» de ${source.artist}`}
        </h1>
        <FileBar file={file} durationSeconds={duration} busy={busy} onFile={studio.analyse} onCancel={studio.cancel} />
        {studio.audioUrl !== null && (
          <QueryPlayer
            url={studio.audioUrl}
            title={file.name}
            spans={layout?.spans ?? []}
            playingIndex={playing?.side === "A" ? playing.index : null}
            onPlaySpan={(index) => play(index, "A")}
          />
        )}
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
            onSelect={select}
            onPlay={play}
            playing={playing}
            analyzing={busy}
            uploadPlayhead={selected === null ? null : player.uploadTime}
            originalPlayhead={selected === null ? null : player.originalTime}
          />
        )}
        {waveform.status === "unavailable" && <p className="quiet-note">Este navegador no pudo dibujar la forma de onda de este archivo; el análisis no cambia.</p>}
        {detection !== null && layout !== null && layout.spans.length > 0 && (
          <div className="findings">
            <MatchTable spans={layout.spans} selected={selected?.index ?? null} threshold={detection.threshold} playing={playing} onPlay={play} />
          </div>
        )}
        {detection !== null && (hiddenWeak > 0 || showWeak) && (
          <button type="button" className="button button--quiet weak-toggle" aria-pressed={showWeak} onClick={() => setShowWeak(!showWeak)}>
            {showWeak ? "Ocultar coincidencias débiles" : `Mostrar coincidencias débiles (${hiddenWeak})`}
          </button>
        )}
        {player.failure !== null && (
          <p className="listen__failure" role="alert">
            No se pudo reproducir: {player.failure}
          </p>
        )}
        {selected !== null && studio.audioUrl !== null && (
          <>
            <audio ref={player.uploadRef} src={studio.audioUrl} preload="auto" data-group="ab" />
            <audio ref={player.originalRef} src={songAudioUrl(selected.match.song.id)} preload="metadata" data-group="ab" />
          </>
        )}
        {phase.kind === "done" && <StageTimings serverTiming={phase.result.serverTiming} engineTimings={phase.result.detection.engine_timings} />}
      </div>
    </div>
  );
}
