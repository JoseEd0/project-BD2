import { useCallback, useRef } from "react";

import { barlineTimes } from "../analysis/ruler";
import type { SpanLayout, TimelineLayout } from "../analysis/spans";
import { OriginalStaff } from "./OriginalStaff";
import { percent } from "./position";
import { Ruler } from "./Ruler";
import { StaffLines } from "./StaffLines";
import { Swipe } from "./Swipe";
import { WaveformPath } from "./WaveformPath";

/** Más barras de compás que esto se vuelven una mancha gris en un teléfono. */
const MAX_BARLINES = 96;

interface TimelineProps {
  readonly durationSeconds: number;
  readonly hopSeconds: number;
  readonly peaks: Float32Array | null;
  readonly layout: TimelineLayout | null;
  readonly selected: SpanLayout | null;
  readonly onSelect: (index: number) => void;
  readonly analyzing: boolean;
  readonly uploadPlayhead: number | null;
  readonly originalPlayhead: number | null;
}

/**
 * La partitura marcada: la forma de onda de la subida sobre un pentagrama con una barra por
 * salto de la detección, cada coincidencia como un trazo de resaltador y, al elegir una, el
 * pentagrama del original debajo, unido por una llave al mismo pasaje.
 */
export function Timeline(props: TimelineProps) {
  const { durationSeconds, hopSeconds, peaks, layout, selected, onSelect, analyzing, uploadPlayhead, originalPlayhead } = props;
  const swipes = useRef<HTMLDivElement>(null);
  const step = useCallback(
    (from: number, direction: 1 | -1) => {
      const count = layout?.spans.length ?? 0;
      if (count === 0) {
        return;
      }
      const next = (from + direction + count) % count;
      onSelect(next);
      swipes.current?.querySelector<HTMLButtonElement>(`[data-swipe-index="${next}"]`)?.focus();
    },
    [layout, onSelect],
  );
  const playedShare = uploadPlayhead === null ? null : uploadPlayhead / durationSeconds;
  return (
    <section className="score" aria-label="Línea de tiempo de tu audio" data-analyzing={analyzing}>
      <div className="staff">
        <p className="staff__label">
          <span className="side-tag">A</span> Tu audio
        </p>
        <div className="staff__body">
          <StaffLines durationSeconds={durationSeconds} barlines={barlineTimes(durationSeconds, hopSeconds, MAX_BARLINES)} />
          {peaks === null ? <div className="staff__pending" /> : <WaveformPath peaks={peaks} playedShare={playedShare} />}
          {analyzing && <div className="staff__scan" aria-hidden="true" />}
          {layout !== null && layout.spans.length > 0 && (
            <div ref={swipes} className="swipes" role="group" aria-label="Tramos que coinciden; flechas para recorrerlos">
              {layout.spans.map((span) => (
                <Swipe
                  key={span.index}
                  span={span}
                  lanes={layout.lanes}
                  selected={selected?.index === span.index}
                  onSelect={onSelect}
                  onStep={step}
                />
              ))}
            </div>
          )}
          {playedShare !== null && <div className="playhead" style={{ left: percent(playedShare) }} aria-hidden="true" />}
        </div>
        <Ruler durationSeconds={durationSeconds} />
      </div>
      {selected !== null && <OriginalStaff span={selected} uploadDuration={durationSeconds} playhead={originalPlayhead} />}
    </section>
  );
}
