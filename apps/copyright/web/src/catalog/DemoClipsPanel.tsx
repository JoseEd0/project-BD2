import { useEffect, useState } from "react";

import { demoClipAudioUrl, useDemoClips } from "../api/catalog";
import type { DemoClip } from "../api/types";
import { Icon } from "../components/Icon";
import { usePlayer } from "../player/usePlayer";

function expectation(clip: DemoClip): string {
  const count = clip.expected_songs.length;
  if (count === 0) {
    return "Sin música del catálogo";
  }
  return count === 1 ? "Contiene 1 canción del catálogo" : `Contiene ${count} canciones del catálogo`;
}

interface DemoClipsPanelProps {
  readonly onAnalyse: (clip: DemoClip) => void;
}

/** Audios de prueba armados con canciones del catálogo: escucharlos y analizarlos con un clic. */
export function DemoClipsPanel({ onAnalyse }: DemoClipsPanelProps) {
  const clips = useDemoClips();
  const [previewing, setPreviewing] = useState<DemoClip | null>(null);
  const preview = usePlayer(previewing === null ? null : demoClipAudioUrl(previewing));

  useEffect(() => {
    if (previewing !== null) {
      preview.audioRef.current?.play().catch(() => setPreviewing(null));
    }
  }, [previewing, preview.audioRef]);

  const togglePreview = (clip: DemoClip) => {
    if (previewing?.id === clip.id) {
      preview.toggle();
      return;
    }
    setPreviewing(clip);
  };

  if (clips.data === undefined || clips.data.length === 0) {
    return null;
  }
  return (
    <section className="demo-clips" aria-labelledby="demo-clips-title">
      <h2 id="demo-clips-title" className="catalog__title">
        Audios de prueba
      </h2>
      <ul className="demo-clips__list" aria-label="Audios de prueba">
        {clips.data.map((clip) => {
          const playing = previewing?.id === clip.id && preview.playing;
          return (
            <li key={clip.id} className="demo-clip">
              <button type="button" className="song__play" onClick={() => togglePreview(clip)} aria-label={playing ? `Pausar ${clip.title}` : `Escuchar ${clip.title}`}>
                <Icon name={playing ? "pause" : "play"} />
              </button>
              <div className="song__text">
                <span className="song__title">{clip.title}</span>
                <span className="demo-clip__description">{clip.description}</span>
                <span className="song__meta">{expectation(clip)}</span>
              </div>
              <button type="button" className="button button--find" onClick={() => onAnalyse(clip)}>
                <Icon name="similar" />
                Analizar
              </button>
            </li>
          );
        })}
      </ul>
      <audio ref={preview.audioRef} src={previewing === null ? undefined : demoClipAudioUrl(previewing)} preload="none" data-group="preview" />
    </section>
  );
}
