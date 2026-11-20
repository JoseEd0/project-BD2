import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useCamera } from "../camera/useCamera";
import { CameraProblemView } from "../components/CameraProblemView";
import { CameraStage } from "../components/CameraStage";
import { FaceOverlay } from "../components/FaceOverlay";
import { Icon } from "../components/Icon";
import { StatusPill } from "../components/StatusPill";
import { StreamBanner } from "../components/StreamBanner";
import { RECOGNITION_SCHEDULER } from "../config";
import type { StageTimings, StreamFace } from "../realtime/protocol";
import type { FrameAnswer } from "../realtime/stream";
import { useRecognitionStream } from "../realtime/useRecognitionStream";

/** Una respuesta más vieja que esto ya no sigue a la cara: sus cajas se atenúan. */
const STALE_ANSWER_MS = 1_200;
const MILLISECONDS = new Intl.NumberFormat("es", { maximumFractionDigits: 0 });

export function recognitionSummary(faces: readonly StreamFace[]): string {
  if (faces.length === 0) {
    return "Apunta la cámara a una cara.";
  }
  const known = faces.filter((face) => face.status === "known" && face.displayName !== null).map((face) => face.displayName);
  const unknown = faces.filter((face) => face.status === "unknown").length;
  const far = faces.filter((face) => face.status === "too_small").length;
  const parts = [
    known.length > 0 ? known.join(", ") : null,
    unknown > 0 ? (unknown === 1 ? "1 desconocido" : `${unknown} desconocidos`) : null,
    far > 0 ? (far === 1 ? "1 muy lejos" : `${far} muy lejos`) : null,
  ];
  return parts.filter((part) => part !== null).join(" · ");
}

/** Los tiempos del servidor; si todas las caras salieron de su rastro, no hubo búsqueda que medir. */
export function timingsSummary(timings: StageTimings, faces: readonly StreamFace[]): string {
  const served = `Servidor ${MILLISECONDS.format(timings.total)} ms · detectar ${MILLISECONDS.format(timings.detect)} ms`;
  const fromTracks = faces.length > 0 && faces.every((face) => face.cached);
  return fromTracks ? `${served} · nombre del rastro, sin buscar` : `${served} · buscar ${MILLISECONDS.format(timings.embed + timings.search)} ms`;
}

function useStale(answer: FrameAnswer | null): boolean {
  const [stale, setStale] = useState(false);
  useEffect(() => {
    setStale(false);
    if (answer === null) {
      return;
    }
    const timer = window.setTimeout(() => setStale(true), STALE_ANSWER_MS);
    return () => window.clearTimeout(timer);
  }, [answer]);
  return stale;
}

export default function RecognizePage() {
  const video = useRef<HTMLVideoElement>(null);
  const camera = useCamera(video, "environment");
  const [paused, setPaused] = useState(false);
  const live = camera.status.kind === "live" && !paused;
  const stream = useRecognitionStream({ video, active: live, scheduler: RECOGNITION_SCHEDULER });
  const stale = useStale(stream.answer);
  const faces = stream.answer?.result.faces ?? [];
  const summary = paused ? "En pausa." : recognitionSummary(faces);
  const timings = stream.answer?.result.timings;

  const top = (
    <>
      <Link className="icon-button" to="/" aria-label="Volver al inicio">
        <Icon name="back" />
      </Link>
      <h1 className="bar-title">Reconocer</h1>
      {camera.status.kind === "live" && <StatusPill state={stream.state} />}
    </>
  );

  if (camera.status.kind === "problem") {
    return (
      <CameraStage
        video={video}
        mirrored={camera.mirrored}
        label="Cámara"
        top={top}
        middle={<CameraProblemView problem={camera.status.problem} onRetry={camera.retry} />}
        sheet={null}
      />
    );
  }

  return (
    <CameraStage
      video={video}
      mirrored={camera.mirrored}
      label="Lo que ve la cámara"
      top={top}
      overlay={(view) => <FaceOverlay answer={stream.answer} view={view} mirrored={camera.mirrored} stale={stale || paused} />}
      middle={<StreamBanner state={stream.state} onRetryNow={stream.resume} />}
      sheet={
        <>
          <title>Reconocer · Rostros</title>
          <p className="camera-summary" aria-live="polite">
            {camera.status.kind === "starting" ? "Abriendo la cámara…" : summary}
          </p>
          <p className="instruction-detail tabular">
            {timings === undefined
              ? "Las cajas verdes son personas registradas; las naranjas, desconocidas."
              : timingsSummary(timings, faces)}
          </p>
          <div className="camera-controls">
            {camera.canFlip ? (
              <button type="button" className="icon-button" onClick={camera.flip} aria-label={camera.facing === "user" ? "Usar la cámara trasera" : "Usar la cámara frontal"}>
                <Icon name="flip" />
              </button>
            ) : (
              <span className="placeholder" />
            )}
            <button type="button" className="button button-primary" onClick={() => setPaused((current) => !current)} aria-pressed={paused}>
              <Icon name={paused ? "play" : "pause"} />
              {paused ? "Seguir" : "Pausar"}
            </button>
            <Link className="icon-button" to="/registro" aria-label="Registrarme">
              <Icon name="user" />
            </Link>
          </div>
        </>
      }
    />
  );
}
