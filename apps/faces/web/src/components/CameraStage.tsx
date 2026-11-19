import { useRef, type ReactNode, type RefObject } from "react";

import type { Size } from "../camera/geometry";
import { useViewSize } from "../camera/useViewSize";

export interface CameraStageProps {
  readonly video: RefObject<HTMLVideoElement | null>;
  readonly mirrored: boolean;
  readonly label: string;
  readonly top: ReactNode;
  /** Lo que se dibuja sobre el video, con el tamaño de la vista. */
  readonly overlay?: (view: Size) => ReactNode;
  readonly middle?: ReactNode;
  /** `null` sin hoja: cuando un problema ocupa la pantalla. */
  readonly sheet: ReactNode;
  /** Cambia con cada toma aceptada: el visor destella una vez. */
  readonly flashKey?: number;
}

/** El escenario de la cámara: video a pantalla completa, arriba lectura, abajo los controles. */
export function CameraStage({ video, mirrored, label, top, overlay, middle, sheet, flashKey = 0 }: CameraStageProps) {
  const stage = useRef<HTMLDivElement>(null);
  const view = useViewSize(stage);
  return (
    <div className="camera" ref={stage}>
      <video
        ref={video}
        className={mirrored ? "camera-video is-mirrored" : "camera-video"}
        autoPlay
        playsInline
        muted
        aria-label={label}
      />
      {view !== null && overlay?.(view)}
      {flashKey > 0 && <div key={flashKey} className="camera-flash is-firing" aria-hidden="true" />}
      <div className="camera-top">{top}</div>
      <div className="camera-middle">{middle}</div>
      {sheet !== null && (
        <div className="camera-sheet">
          <div className="camera-sheet-inner">{sheet}</div>
        </div>
      )}
    </div>
  );
}
