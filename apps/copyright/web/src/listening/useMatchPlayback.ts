import { useCallback, useEffect, useState } from "react";

import type { SpanLayout, TimelineLayout } from "../analysis/spans";
import { useAbPlayer, type AbPlayer } from "./useAbPlayer";
import type { Side } from "./sync";

export interface PlayingMatch {
  readonly index: number;
  readonly side: Side;
}

export interface MatchPlayback {
  readonly selected: SpanLayout | null;
  readonly select: (index: number) => void;
  readonly player: AbPlayer;
  readonly playing: PlayingMatch | null;
  /** Elige la coincidencia `index` y la hace sonar por `side`; si ya suena así, pausa. */
  readonly play: (index: number, side: Side) => void;
}

/**
 * Un clic en una coincidencia la elige y la escucha: la elección cambia el original que
 * carga el reproductor A/B, así que el sonido arranca después de esa elección.
 */
export function useMatchPlayback(layout: TimelineLayout | null): MatchPlayback {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [pending, setPending] = useState<Side | null>(null);
  const selected = selectedIndex === null ? null : (layout?.spans[selectedIndex] ?? null);
  const player = useAbPlayer(selected?.match ?? null);

  useEffect(() => {
    setSelectedIndex(layout !== null && layout.spans.length > 0 ? 0 : null);
  }, [layout]);

  useEffect(() => {
    if (pending !== null && selected !== null) {
      setPending(null);
      player.playSide(pending);
    }
  }, [pending, player, selected]);

  const play = useCallback(
    (index: number, side: Side) => {
      if (index === selectedIndex) {
        player.playSide(side);
        return;
      }
      setSelectedIndex(index);
      setPending(side);
    },
    [player, selectedIndex],
  );

  const playing = player.playing && selected !== null ? { index: selected.index, side: player.side } : null;
  return { selected, select: setSelectedIndex, player, playing, play };
}
