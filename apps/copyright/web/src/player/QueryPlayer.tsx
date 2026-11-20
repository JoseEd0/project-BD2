import { formatClock } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";
import { Icon } from "../components/Icon";
import { SKIP_SECONDS, usePlayer } from "./usePlayer";

interface QueryPlayerProps {
  readonly url: string;
  readonly title: string;
  readonly spans: readonly SpanLayout[];
}

/**
 * El audio elegido, entero, con sus controles y los tramos que coinciden marcados sobre la
 * barra: un clic en un tramo salta a su inicio.
 */
export function QueryPlayer({ url, title, spans }: QueryPlayerProps) {
  const player = usePlayer(url);
  const duration = player.duration ?? 0;
  return (
    <section className="deck" aria-label={`Reproductor de ${title}`}>
      <div className="deck__controls">
        <button type="button" className="deck__skip" onClick={() => player.skip(-SKIP_SECONDS)} aria-label={`Retroceder ${SKIP_SECONDS} s`}>
          <Icon name="back" />
        </button>
        <button type="button" className="deck__play" onClick={player.toggle} aria-label={player.playing ? "Pausar el audio elegido" : "Reproducir el audio elegido"}>
          <Icon name={player.playing ? "pause" : "play"} size={26} />
        </button>
        <button type="button" className="deck__skip" onClick={() => player.skip(SKIP_SECONDS)} aria-label={`Adelantar ${SKIP_SECONDS} s`}>
          <Icon name="forward" />
        </button>
      </div>
      <div className="deck__track">
        <div className="deck__bands" aria-hidden="true">
          {spans.map((span) => (
            <button
              key={span.index}
              type="button"
              tabIndex={-1}
              className="deck__band"
              data-ink={span.ink}
              data-weak={span.weak}
              style={{ left: `${span.startShare * 100}%`, width: `${span.widthShare * 100}%` }}
              onClick={() => player.seekTo(span.match.upload.start_seconds)}
            >
              {span.letter}
            </button>
          ))}
        </div>
        <input
          className="deck__scrub"
          type="range"
          min={0}
          max={duration || 1}
          step={0.1}
          value={Math.min(player.time, duration || 1)}
          onChange={(event) => player.seekTo(Number(event.target.value))}
          aria-label="Posición en el audio elegido"
          aria-valuetext={`${formatClock(player.time)} de ${formatClock(duration)}`}
          style={{ "--played": `${duration > 0 ? (player.time / duration) * 100 : 0}%` } as React.CSSProperties}
        />
      </div>
      <p className="deck__clock measure" aria-live="off">
        {formatClock(player.time)} <span className="deck__total">/ {formatClock(duration)}</span>
      </p>
      {player.failure !== null && (
        <p className="listen__failure" role="alert">
          No se pudo reproducir: {player.failure}
        </p>
      )}
      <audio ref={player.audioRef} src={url} preload="metadata" data-group="query" />
    </section>
  );
}
