import { formatClock, formatOffset, formatSpan } from "../analysis/format";
import type { SpanLayout } from "../analysis/spans";
import { useSong } from "../api/useSong";
import { percent } from "./position";
import { Ruler } from "./Ruler";

interface OriginalStaffProps {
  readonly span: SpanLayout;
  readonly uploadDuration: number;
  readonly playhead: number | null;
}

/** La llave entre los dos pentagramas: une el pasaje de la subida con el del original. */
function Bracket({ upload, original }: { readonly upload: readonly [number, number]; readonly original: readonly [number, number] }) {
  const points = `${upload[0] * 100},0 ${upload[1] * 100},0 ${original[1] * 100},100 ${original[0] * 100},100`;
  return (
    <svg className="bracket" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <polygon className="bracket__fill" points={points} />
      <line className="bracket__edge" x1={upload[0] * 100} y1={0} x2={original[0] * 100} y2={100} vectorEffect="non-scaling-stroke" />
      <line className="bracket__edge" x1={upload[1] * 100} y1={0} x2={original[1] * 100} y2={100} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * El original de la coincidencia elegida, a su propia escala: su duración sale del catálogo
 * (`GET /songs/{id}`); mientras llega, el final del tramo.
 */
export function OriginalStaff({ span, uploadDuration, playhead }: OriginalStaffProps) {
  const { match } = span;
  const song = useSong(match.song.id);
  const duration = Math.max(song.data?.duration_seconds ?? match.original.end_seconds, match.original.end_seconds);
  const originalShare = [match.original.start_seconds / duration, match.original.end_seconds / duration] as const;
  const uploadShare = [match.upload.start_seconds / uploadDuration, match.upload.end_seconds / uploadDuration] as const;
  return (
    <>
      <Bracket upload={uploadShare} original={originalShare} />
      <div className="staff staff--original">
        <p className="staff__label">
          <span className="side-tag" data-ink={span.ink}>
            B
          </span>{" "}
          <span className="staff__song">
            Original: {match.song.title} <span className="staff__artist">— {match.song.artist}</span>
          </span>
          <span className="staff__meta measure">
            {formatSpan(match.original.start_seconds, match.original.end_seconds)} de {formatClock(duration)} · desfase {formatOffset(match.offset_seconds)}
            {song.data !== undefined && ` · ${song.data.genre}`}
          </span>
        </p>
        <div className="staff__body staff__body--original">
          <div className="original-track" aria-hidden="true" />
          <div
            className="original-span"
            data-ink={span.ink}
            data-weak={span.weak}
            style={{ left: percent(originalShare[0]), width: percent(originalShare[1] - originalShare[0]) }}
            aria-hidden="true"
          >
            <span className="song-chip" data-ink={span.ink}>
              {span.letter}
            </span>
          </div>
          {playhead !== null && <div className="playhead" style={{ left: percent(playhead / duration) }} aria-hidden="true" />}
        </div>
        <Ruler durationSeconds={duration} />
      </div>
    </>
  );
}
