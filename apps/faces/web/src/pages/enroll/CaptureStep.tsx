import { useEffect, useRef } from "react";

import { submitShot, type Enrollment, type ShotResult } from "../../api/faces";
import type { ApiProblem } from "../../api/problems";
import { captureJpeg } from "../../camera/capture";
import { useCamera } from "../../camera/useCamera";
import { CameraProblemView } from "../../components/CameraProblemView";
import { CameraStage } from "../../components/CameraStage";
import { Icon } from "../../components/Icon";
import { OvalGuide } from "../../components/OvalGuide";
import { ShotStrip, poseName } from "../../components/ShotStrip";
import { StatusPill } from "../../components/StatusPill";
import { GUIDE_SCHEDULER, SHOT_JPEG } from "../../config";
import type { ShotFeedback } from "../../enrollment/machine";
import { PRECHECK_MESSAGES, SHOT_GATE, ShotGate, precheck, type PrecheckIssue } from "../../enrollment/precheck";
import { useRecognitionStream } from "../../realtime/useRecognitionStream";

/** Cada cuánto se pregunta a la compuerta si ya toca disparar. */
const GATE_TICK_MS = 200;

export interface CaptureStepProps {
  readonly enrollment: Enrollment;
  readonly shooting: boolean;
  readonly feedback: ShotFeedback | null;
  readonly accepted: number;
  readonly onShotSent: () => void;
  readonly onShotAnswered: (shot: ShotResult) => void;
  readonly onShotFailed: (problem: ApiProblem) => void;
  readonly onCancel: () => void;
}

function detailOf(feedback: ShotFeedback | null): { text: string; rejected: boolean } {
  if (feedback === null) {
    return { text: "La foto se toma sola cuando tu cara está quieta dentro del óvalo.", rejected: false };
  }
  if (feedback.tone === "accepted") {
    return { text: `Lista la toma «${poseName(feedback.pose)}».`, rejected: false };
  }
  return { text: feedback.message, rejected: true };
}

/** Las tres tomas guiadas: el socket dice dónde está la cara, la compuerta decide cuándo disparar. */
export function CaptureStep(props: CaptureStepProps) {
  const { enrollment, shooting, feedback, accepted, onCancel } = props;
  const video = useRef<HTMLVideoElement>(null);
  const camera = useCamera(video, "user");
  const live = camera.status.kind === "live";
  const guide = useRecognitionStream({ video, active: live, scheduler: GUIDE_SCHEDULER });
  const gate = useRef<ShotGate | null>(null);
  gate.current ??= new ShotGate(performance.now(), SHOT_GATE);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const latest = useRef(props);
  latest.current = props;

  const issue: PrecheckIssue | null = guide.answer === null ? null : precheck(guide.answer.result.faces, guide.answer.size);
  const hintFresh = guide.state.phase === "live" && guide.answer !== null;

  useEffect(() => {
    if (guide.answer !== null) {
      gate.current?.observe(precheck(guide.answer.result.faces, guide.answer.size), performance.now());
    }
  }, [guide.answer]);

  const shoot = async () => {
    const element = video.current;
    if (element === null || latest.current.shooting) {
      return;
    }
    canvas.current ??= document.createElement("canvas");
    const photo = await captureJpeg(element, canvas.current, SHOT_JPEG);
    if (photo === null) {
      return;
    }
    gate.current?.shot(performance.now());
    latest.current.onShotSent();
    const outcome = await submitShot(latest.current.enrollment.enrollment_id, photo.blob);
    gate.current?.answered(outcome.ok && outcome.data.accepted, performance.now());
    if (outcome.ok) {
      latest.current.onShotAnswered(outcome.data);
    } else {
      latest.current.onShotFailed(outcome.problem);
    }
  };
  const shootRef = useRef(shoot);
  shootRef.current = shoot;

  useEffect(() => {
    if (!live) {
      return;
    }
    const timer = window.setInterval(() => {
      if (!latest.current.shooting && gate.current?.shouldShoot(performance.now())) {
        void shootRef.current();
      }
    }, GATE_TICK_MS);
    return () => window.clearInterval(timer);
  }, [live]);

  const current = enrollment.remaining[0] ?? null;
  const instruction = !live
    ? "Abriendo la cámara…"
    : shooting
      ? "Quieto… tomando la foto"
      : hintFresh && issue !== null
        ? PRECHECK_MESSAGES[issue]
        : enrollment.message;
  const detail = detailOf(feedback);

  const top = (
    <>
      <button type="button" className="icon-button" onClick={onCancel} aria-label="Cancelar el registro">
        <Icon name="close" />
      </button>
      <h1 className="bar-title">Registro de {enrollment.display_name}</h1>
      {live && <StatusPill state={guide.state} />}
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
      label="Tu cara en la cámara"
      top={top}
      flashKey={accepted}
      overlay={(view) => <OvalGuide view={view} ready={hintFresh && issue === null} pose={current} mirrored={camera.mirrored} />}
      sheet={
        <>
          <title>Registro · Rostros</title>
          <p className="instruction" aria-live="polite">
            {shooting && <span className="spinner" aria-hidden="true" />}
            {instruction}
          </p>
          <p className={detail.rejected ? "instruction-detail is-rejected" : "instruction-detail"} role={detail.rejected ? "alert" : undefined}>
            {detail.text}
          </p>
          <ShotStrip poses={enrollment.poses} covered={enrollment.covered} current={current} />
          <div className="camera-controls">
            {camera.canFlip ? (
              <button type="button" className="icon-button" onClick={camera.flip} aria-label={camera.facing === "user" ? "Usar la cámara trasera" : "Usar la cámara frontal"}>
                <Icon name="flip" />
              </button>
            ) : (
              <span className="placeholder" />
            )}
            <button type="button" className="button button-secondary" onClick={() => void shoot()} disabled={!live || shooting}>
              <Icon name="camera" />
              Tomar ahora
            </button>
            <span className="placeholder" />
          </div>
          <p className="attempts tabular">Te quedan {enrollment.attempts_left} intentos</p>
        </>
      }
    />
  );
}
