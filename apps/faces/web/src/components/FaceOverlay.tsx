import { frameRectToView, placeLabel, type Size } from "../camera/geometry";
import type { StreamFace } from "../realtime/protocol";
import type { FrameAnswer } from "../realtime/stream";

/** Lo que se estima de una placa antes de dibujarla: alto de una línea y ancho por carácter. */
const LABEL_HEIGHT = 28;
const LABEL_CHAR_WIDTH = 8.6;
const LABEL_PADDING = 22;
const LABEL_GAP = 6;
const BOX_RADIUS = 12;
const PERCENT = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 0 });

export function faceCaption(face: StreamFace): { text: string; score: string | null; spoken: string } {
  if (face.status === "known" && face.displayName !== null) {
    const score = face.score === null ? null : PERCENT.format(Math.max(0, face.score));
    return { text: face.displayName, score, spoken: score === null ? face.displayName : `${face.displayName}, parecido ${score}` };
  }
  if (face.status === "too_small") {
    return { text: face.label || "acércate", score: null, spoken: "rostro muy lejos: acércate" };
  }
  return { text: "desconocido", score: null, spoken: "persona desconocida" };
}

export interface FaceOverlayProps {
  readonly answer: FrameAnswer | null;
  readonly view: Size;
  readonly mirrored: boolean;
  /** Las cajas de una respuesta vieja se atenúan: ya no siguen a nadie. */
  readonly stale: boolean;
}

/** Cada rostro con su caja y su placa, como el pie impreso de una tira de fotos. */
export function FaceOverlay({ answer, view, mirrored, stale }: FaceOverlayProps) {
  if (answer === null) {
    return null;
  }
  const faces = answer.result.faces.map((face, index) => {
    const box = frameRectToView(face.box, answer.size, view, mirrored);
    const caption = faceCaption(face);
    const labelWidth = (caption.text.length + (caption.score?.length ?? 0) + 1) * LABEL_CHAR_WIDTH + LABEL_PADDING;
    const place = placeLabel(box, { width: labelWidth, height: LABEL_HEIGHT }, view, LABEL_GAP);
    return { key: face.track ?? `i${index}`, face, box, caption, place };
  });
  return (
    <div className={stale ? "face-layer is-stale" : "face-layer"} data-testid="face-layer">
      <svg className="camera-overlay" viewBox={`0 0 ${view.width} ${view.height}`} aria-hidden="true">
        {faces.map(({ key, face, box }) => (
          <rect
            key={key}
            className="face-box"
            data-status={face.status}
            x={box.x}
            y={box.y}
            width={box.width}
            height={box.height}
            rx={BOX_RADIUS}
          />
        ))}
      </svg>
      {faces.map(({ key, face, caption, place }) => (
        <span
          key={key}
          className="face-label"
          data-status={face.status}
          style={{ transform: `translate(${place.x}px, ${place.y}px)` }}
          aria-label={caption.spoken}
        >
          {caption.text}
          {caption.score !== null && <span className="score">{caption.score}</span>}
        </span>
      ))}
    </div>
  );
}
