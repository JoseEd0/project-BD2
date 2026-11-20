import { formatOffset, formatPercent, formatSpan } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";
import type { Side } from "../listening/sync";

interface MatchTableProps {
  readonly spans: readonly SpanLayout[];
  readonly selected: number | null;
  readonly threshold: number;
  readonly playing: { readonly index: number; readonly side: Side } | null;
  readonly onPlay: (index: number, side: Side) => void;
}

/** Las coincidencias con sus cifras; la fila elegida es el trazo elegido. */
export function MatchTable({ spans, selected, threshold, playing, onPlay }: MatchTableProps) {
  return (
    <table className="matches">
      <caption className="matches__caption">
        Coincidencias, de mayor a menor evidencia. Clic en una letra para escuchar tu audio en ese tramo, o en el tiempo del original para oír el original. Copia probable desde {formatPercent(threshold)}.
      </caption>
      <thead>
        <tr>
          <th scope="col">Canción original</th>
          <th scope="col">En tu audio</th>
          <th scope="col">En el original</th>
          <th scope="col">Desfase</th>
          <th scope="col">Evidencia</th>
        </tr>
      </thead>
      <tbody>
        {spans.map((span) => {
          const { match } = span;
          const playingSide = playing?.index === span.index ? playing.side : null;
          return (
            <tr key={span.index} aria-selected={selected === span.index} data-weak={span.weak}>
              <th scope="row">
                <button
                  type="button"
                  className="matches__pick"
                  aria-pressed={selected === span.index}
                  data-playing={playingSide === "A"}
                  aria-label={`${playingSide === "A" ? "Pausar" : "Escuchar en tu audio"} ${span.letter}: ${match.song.title}`}
                  onClick={() => onPlay(span.index, "A")}
                >
                  <span className="song-chip" data-ink={span.ink} data-weak={span.weak}>
                    {playingSide === "A" ? "❚❚" : "▶"} {span.letter}
                  </span>
                  <span className="matches__song">
                    <span className="matches__title">{match.song.title}</span>
                    <span className="matches__artist">{match.song.artist}</span>
                  </span>
                </button>
              </th>
              <td className="measure" data-label="En tu audio">
                {formatSpan(match.upload.start_seconds, match.upload.end_seconds)}
              </td>
              <td data-label="En el original">
                <button
                  type="button"
                  className="matches__original measure"
                  data-playing={playingSide === "B"}
                  aria-label={`${playingSide === "B" ? "Pausar el original" : "Escuchar el original"} ${match.song.title} en ${formatSpan(match.original.start_seconds, match.original.end_seconds)}`}
                  onClick={() => onPlay(span.index, "B")}
                >
                  {playingSide === "B" ? "❚❚" : "▶"} {formatSpan(match.original.start_seconds, match.original.end_seconds)}
                </button>
              </td>
              <td className="measure" data-label="Desfase">
                {formatOffset(match.offset_seconds)}
              </td>
              <td data-label="Evidencia">
                <span className="measure">{formatPercent(match.score)}</span>{" "}
                <span className="strength">{span.weak ? "débil" : "copia probable"}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
