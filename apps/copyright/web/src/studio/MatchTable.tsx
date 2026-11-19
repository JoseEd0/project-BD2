import { formatOffset, formatPercent, formatSpan } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";

interface MatchTableProps {
  readonly spans: readonly SpanLayout[];
  readonly selected: number | null;
  readonly threshold: number;
  readonly onSelect: (index: number) => void;
}

/** Las coincidencias con sus cifras; la fila elegida es el trazo elegido. */
export function MatchTable({ spans, selected, threshold, onSelect }: MatchTableProps) {
  return (
    <table className="matches">
      <caption className="matches__caption">
        Coincidencias, de mayor a menor parecido. Copia probable desde {formatPercent(threshold)}.
      </caption>
      <thead>
        <tr>
          <th scope="col">Canción original</th>
          <th scope="col">En tu audio</th>
          <th scope="col">En el original</th>
          <th scope="col">Desfase</th>
          <th scope="col">Parecido</th>
        </tr>
      </thead>
      <tbody>
        {spans.map((span) => {
          const { match } = span;
          return (
            <tr key={span.index} aria-selected={selected === span.index} data-weak={span.weak}>
              <th scope="row">
                <button type="button" className="matches__pick" aria-pressed={selected === span.index} onClick={() => onSelect(span.index)}>
                  <span className="song-chip" data-ink={span.ink} data-weak={span.weak}>
                    {span.letter}
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
              <td className="measure" data-label="En el original">
                {formatSpan(match.original.start_seconds, match.original.end_seconds)}
              </td>
              <td className="measure" data-label="Desfase">
                {formatOffset(match.offset_seconds)}
              </td>
              <td data-label="Parecido">
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
