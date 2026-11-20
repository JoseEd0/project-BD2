import type { KeyboardEvent } from "react";

import { formatOffset, formatPercent, formatSpan } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";
import { percent } from "./position";

interface SwipeProps {
  readonly span: SpanLayout;
  readonly lanes: number;
  readonly selected: boolean;
  readonly playing: boolean;
  readonly onPlay: (index: number) => void;
  readonly onStep: (from: number, direction: 1 | -1) => void;
}

/** El escalonado del trazo al llegar el resultado (DESIGN.md, «Movimiento»). */
const SWIPE_STAGGER_MS = 60;
const STEP_KEYS: Record<string, 1 | -1> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

export function swipeLabel(span: SpanLayout): string {
  const { match } = span;
  const strength = span.weak ? "coincidencia débil" : "posible copia";
  return `${span.letter}: ${match.song.title} de ${match.song.artist}. ${strength}, ${formatPercent(match.score)}. En tu audio ${formatSpan(match.upload.start_seconds, match.upload.end_seconds)}, desfase ${formatOffset(match.offset_seconds)}.`;
}

/** Un trazo de resaltador sobre la forma de onda: un botón que elige la coincidencia. */
export function Swipe({ span, lanes, selected, playing, onPlay, onStep }: SwipeProps) {
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const direction = STEP_KEYS[event.key];
    if (direction !== undefined) {
      event.preventDefault();
      onStep(span.index, direction);
    }
  };
  return (
    <button
      type="button"
      className="swipe"
      data-ink={span.ink}
      data-weak={span.weak}
      aria-pressed={selected}
      data-playing={playing}
      aria-label={swipeLabel(span)}
      data-swipe-index={span.index}
      style={{
        left: percent(span.startShare),
        width: percent(span.widthShare),
        top: percent(span.lane / lanes),
        height: percent(1 / lanes),
        ["--delay" as string]: `${span.index * SWIPE_STAGGER_MS}ms`,
        ["--strength" as string]: String(span.match.score),
      }}
      onClick={() => onPlay(span.index)}
      onKeyDown={onKeyDown}
    >
      <span className="swipe__letter">
        {playing ? "❚❚" : "▶"} {span.letter}
      </span>
      <span className="swipe__score measure">{formatPercent(span.match.score)}</span>
    </button>
  );
}
