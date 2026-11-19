import { poseArrowSide, type Pose, type Size } from "../camera/geometry";

/** El óvalo: proporción de una cara, centrado un poco arriba para dejar sitio a la hoja. */
const OVAL_ASPECT = 1.3;
const OVAL_MAX_HEIGHT_FRACTION = 0.48;
const OVAL_MAX_WIDTH_FRACTION = 0.7;
export const OVAL_CENTER_Y_FRACTION = 0.42;
const ARROW_REACH = 34;
const ARROW_HEAD = 14;

export interface OvalGuideProps {
  readonly view: Size;
  readonly ready: boolean;
  readonly pose: Pose | null;
  readonly mirrored: boolean;
}

export function OvalGuide({ view, ready, pose, mirrored }: OvalGuideProps) {
  const ry = Math.min(view.height * OVAL_MAX_HEIGHT_FRACTION, view.width * OVAL_MAX_WIDTH_FRACTION * OVAL_ASPECT) / 2;
  const rx = ry / OVAL_ASPECT;
  const cx = view.width / 2;
  const cy = view.height * OVAL_CENTER_Y_FRACTION;
  const side = pose === null ? null : poseArrowSide(pose, mirrored);
  const direction = side === "left" ? -1 : 1;
  const arrowX = cx + direction * (rx + ARROW_REACH);
  const shade = `M0 0H${view.width}V${view.height}H0Z M${cx - rx} ${cy}a${rx} ${ry} 0 1 0 ${2 * rx} 0a${rx} ${ry} 0 1 0 ${-2 * rx} 0Z`;
  return (
    <svg className="camera-overlay" viewBox={`0 0 ${view.width} ${view.height}`} aria-hidden="true">
      <path className="guide-shade" d={shade} fillRule="evenodd" />
      <ellipse className={ready ? "guide-oval is-ready" : "guide-oval"} cx={cx} cy={cy} rx={rx} ry={ry} />
      {side !== null && (
        <path
          className="guide-arrow"
          data-side={side}
          d={`M${arrowX - direction * ARROW_REACH * 0.6} ${cy}H${arrowX}M${arrowX - direction * ARROW_HEAD} ${cy - ARROW_HEAD}L${arrowX} ${cy}L${arrowX - direction * ARROW_HEAD} ${cy + ARROW_HEAD}`}
        />
      )}
    </svg>
  );
}
