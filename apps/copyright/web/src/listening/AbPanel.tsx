import { useEffect, useRef } from "react";

import { formatSpan } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";
import { songAudioUrl } from "../api/client";
import { Icon } from "../components/Icon";
import type { AbPlayer } from "./useAbPlayer";
import type { Side } from "./sync";

/** Lo que ya atiende el teclado por su cuenta: ahí las teclas del reproductor no se roban. */
const TYPING_TARGETS = "input:not([type='radio']):not([type='checkbox']), textarea, select, [contenteditable='true']";
const ACTIVATING_TARGETS = "button, a, summary, input, select, textarea";

function useListeningKeys(player: AbPlayer): void {
  const latest = useRef(player);
  useEffect(() => {
    latest.current = player;
  });
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || !(event.target instanceof Element)) {
        return;
      }
      if (event.target.closest(TYPING_TARGETS) !== null) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "a" || key === "b") {
        latest.current.choose(key.toUpperCase() as Side);
      } else if (key === "x") {
        latest.current.toggle();
      } else if (key === " " && event.target.closest(ACTIVATING_TARGETS) === null) {
        event.preventDefault();
        latest.current.playPause();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
}

interface AbPanelProps {
  readonly span: SpanLayout;
  readonly uploadUrl: string;
  readonly player: AbPlayer;
}

/** La escucha A/B del tramo elegido: los dos audios desde el mismo punto, en bucle. */
export function AbPanel({ span, uploadUrl, player }: AbPanelProps) {
  useListeningKeys(player);
  const { match } = span;
  return (
    <section className="listen" aria-labelledby="listen-title">
      <h2 id="listen-title" className="panel-title">
        Escucha el tramo {span.letter}
      </h2>
      <div className="listen__deck">
        <button type="button" className="listen__play" onClick={player.playPause} aria-label={player.playing ? "Pausar" : "Reproducir el tramo"}>
          <Icon name={player.playing ? "pause" : "play"} size={26} />
        </button>
        <fieldset className="segmented segmented--ab">
          <legend className="visually-hidden">Qué se oye</legend>
          <label className="segmented__option">
            <input type="radio" name="ab-side" value="A" checked={player.side === "A"} onChange={() => player.choose("A")} />
            <span>
              <strong>A</strong> tu audio <span className="measure">{formatSpan(match.upload.start_seconds, match.upload.end_seconds)}</span>
            </span>
          </label>
          <label className="segmented__option" data-ink={span.ink}>
            <input type="radio" name="ab-side" value="B" checked={player.side === "B"} onChange={() => player.choose("B")} />
            <span>
              <strong>B</strong> original <span className="measure">{formatSpan(match.original.start_seconds, match.original.end_seconds)}</span>
            </span>
          </label>
        </fieldset>
        <button type="button" className="button button--quiet" onClick={player.toggle}>
          <Icon name="swap" />
          Alternar
        </button>
      </div>
      <p className="listen__keys">
        Teclas: <kbd>A</kbd> y <kbd>B</kbd> eligen, <kbd>X</kbd> alterna, <kbd>Espacio</kbd> reproduce o pausa. Los dos suenan a la vez y solo oyes uno: al alternar no se pierde el punto.
      </p>
      {player.failure !== null && (
        <p className="listen__failure" role="alert">
          No se pudo reproducir: {player.failure}
        </p>
      )}
      <audio ref={player.uploadRef} src={uploadUrl} preload="auto" />
      <audio ref={player.originalRef} src={songAudioUrl(match.song.id)} preload="metadata" />
    </section>
  );
}
