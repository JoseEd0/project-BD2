import { useRef, useState } from "react";

import { captureJpeg } from "../camera/capture";
import { useCamera } from "../camera/useCamera";
import { SHOT_JPEG } from "../config";
import { CameraProblemView } from "./CameraProblemView";
import { CameraStage } from "./CameraStage";
import { Icon } from "./Icon";

export interface SelfieCameraProps {
  readonly title: string;
  readonly onCapture: (photo: Blob) => void;
  readonly onCancel: () => void;
}

/**
 * Una sola foto con la cámara: la frontal a pantalla completa, el obturador al alcance del
 * pulgar y, si la cámara no se puede usar, qué pasó y cómo salir. La foto sale como JPEG sin
 * espejar; no se guarda en ningún lado.
 */
export function SelfieCamera({ title, onCapture, onCancel }: SelfieCameraProps) {
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const camera = useCamera(video, "user");
  const [shooting, setShooting] = useState(false);
  const live = camera.status.kind === "live";

  const shoot = async () => {
    const element = video.current;
    if (element === null) {
      return;
    }
    canvas.current ??= document.createElement("canvas");
    setShooting(true);
    const photo = await captureJpeg(element, canvas.current, SHOT_JPEG);
    setShooting(false);
    if (photo !== null) {
      onCapture(photo.blob);
    }
  };

  const top = (
    <>
      <button type="button" className="icon-button" onClick={onCancel} aria-label="Cancelar y volver">
        <Icon name="close" />
      </button>
      <h1 className="bar-title">{title}</h1>
    </>
  );

  if (camera.status.kind === "problem") {
    return (
      <CameraStage
        video={video}
        mirrored={camera.mirrored}
        label="Cámara"
        top={top}
        middle={
          <CameraProblemView
            problem={camera.status.problem}
            onRetry={camera.retry}
            extra={
              <button type="button" className="button button-secondary" onClick={onCancel}>
                Elegir una foto en su lugar
              </button>
            }
          />
        }
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
      sheet={
        <>
          <p className="instruction" aria-live="polite">
            {live ? "Encuadra tu cara y toca el obturador." : "Abriendo la cámara…"}
          </p>
          <div className="camera-controls">
            {camera.canFlip ? (
              <button type="button" className="icon-button" onClick={camera.flip} aria-label={camera.facing === "user" ? "Usar la cámara trasera" : "Usar la cámara frontal"}>
                <Icon name="flip" />
              </button>
            ) : (
              <span className="placeholder" />
            )}
            <button type="button" className="shutter" onClick={() => void shoot()} disabled={!live || shooting} aria-label="Tomar la foto">
              <span aria-hidden="true" />
            </button>
            <span className="placeholder" />
          </div>
        </>
      }
    />
  );
}
