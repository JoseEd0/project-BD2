import { formatClock } from "../analysis/format";
import { rulerTicks } from "../analysis/ruler";
import { percent } from "./position";

/** Marcas que caben a 390 px sin pisarse; en escritorio, el doble. */
const NARROW_TICKS = 5;
const WIDE_TICKS = 10;

export function Ruler({ durationSeconds }: { readonly durationSeconds: number }) {
  const wide = rulerTicks(durationSeconds, WIDE_TICKS);
  const narrow = new Set(rulerTicks(durationSeconds, NARROW_TICKS));
  return (
    <div className="ruler" aria-hidden="true">
      {wide.map((tick) => (
        <span key={tick} className="ruler__tick measure" data-narrow={narrow.has(tick)} style={{ left: percent(tick / durationSeconds) }}>
          {formatClock(tick)}
        </span>
      ))}
    </div>
  );
}
