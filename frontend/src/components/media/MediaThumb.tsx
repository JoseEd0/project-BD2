import { mediaFileUrl, thumbnailUrl } from "../../api";
import type { MediaKind } from "../../lib/mediaSearch";

interface MediaThumbProps {
  path: string;
  kind: MediaKind;
  label: string;
  /** Un audio se puede reproducir; en una lista para elegir, solo se nombra. */
  playable?: boolean;
}

/** La miniatura de una imagen o el reproductor de un audio, servidos por el API. */
export default function MediaThumb({ path, kind, label, playable = true }: MediaThumbProps) {
  if (kind === "image") {
    return <img alt={label} className="thumb" decoding="async" loading="lazy" src={thumbnailUrl(path)} />;
  }
  if (!playable) {
    return (
      <span aria-hidden="true" className="thumb thumb--audio">
        ♪
      </span>
    );
  }
  return <audio aria-label={label} className="player" controls preload="none" src={mediaFileUrl(path)} />;
}
