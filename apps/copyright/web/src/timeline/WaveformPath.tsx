import { memo, useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { reducePeaks } from "../audio/peaks";

/** Ancho del lienzo en unidades del `viewBox`: el SVG se estira al ancho real. */
export const CANVAS_WIDTH = 1000;
const CANVAS_HEIGHT = 100;
const MIDLINE = CANVAS_HEIGHT / 2;
const BAR_FILL = 0.62;
/** Píxeles por barra: más juntas, la forma de onda se vuelve una mancha en un teléfono. */
const PIXELS_PER_BAR = 3;
/** Un audio en silencio aún se ve como una línea, no como nada. */
const MIN_BAR_HALF_HEIGHT = 0.6;

function barsPath(peaks: Float32Array): string {
  const step = CANVAS_WIDTH / peaks.length;
  let path = "";
  peaks.forEach((peak, index) => {
    const x = (index + 0.5) * step;
    const half = Math.max(MIN_BAR_HALF_HEIGHT, peak * MIDLINE * 0.92);
    path += `M${x.toFixed(2)} ${(MIDLINE - half).toFixed(2)}V${(MIDLINE + half).toFixed(2)}`;
  });
  return path;
}

interface WaveformPathProps {
  readonly peaks: Float32Array;
  /** Hasta dónde ya sonó, entre 0 y 1: esa parte se pinta con tinta plena. */
  readonly playedShare: number | null;
}

/** La forma de onda como barras simétricas sobre la línea media del pentagrama. */
function useWidth(element: RefObject<SVGSVGElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const target = element.current;
    if (target === null || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? null));
    observer.observe(target);
    return () => observer.disconnect();
  }, [element]);
  return width;
}

export const WaveformPath = memo(function WaveformPath({ peaks, playedShare }: WaveformPathProps) {
  const svg = useRef<SVGSVGElement>(null);
  const width = useWidth(svg);
  const bars = useMemo(() => (width === null ? peaks : reducePeaks(peaks, width / PIXELS_PER_BAR)), [peaks, width]);
  const path = useMemo(() => barsPath(bars), [bars]);
  const strokeWidth = (CANVAS_WIDTH / Math.max(1, bars.length)) * BAR_FILL;
  return (
    <svg ref={svg} className="waveform" viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id="waveform-played">
          <rect x={0} y={0} width={(playedShare ?? 0) * CANVAS_WIDTH} height={CANVAS_HEIGHT} />
        </clipPath>
      </defs>
      <path className="waveform__bars" d={path} strokeWidth={strokeWidth} />
      {playedShare !== null && <path className="waveform__played" d={path} strokeWidth={strokeWidth} clipPath="url(#waveform-played)" />}
    </svg>
  );
});
