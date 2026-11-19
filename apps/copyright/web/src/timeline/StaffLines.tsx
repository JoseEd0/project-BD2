import { memo } from "react";

import { CANVAS_WIDTH } from "./WaveformPath";

const STAFF_LINES = [10, 30, 50, 70, 90];

interface StaffLinesProps {
  readonly durationSeconds: number;
  readonly barlines: readonly number[];
}

/** Cinco líneas tenues y una barra de compás por salto de la detección. */
export const StaffLines = memo(function StaffLines({ durationSeconds, barlines }: StaffLinesProps) {
  return (
    <svg className="staff-lines" viewBox={`0 0 ${CANVAS_WIDTH} 100`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      {STAFF_LINES.map((y) => (
        <line key={y} x1={0} x2={CANVAS_WIDTH} y1={y} y2={y} vectorEffect="non-scaling-stroke" />
      ))}
      {barlines.map((time) => {
        const x = (time / durationSeconds) * CANVAS_WIDTH;
        return <line key={time} className="staff-lines__bar" x1={x} x2={x} y1={10} y2={90} vectorEffect="non-scaling-stroke" />;
      })}
    </svg>
  );
});
