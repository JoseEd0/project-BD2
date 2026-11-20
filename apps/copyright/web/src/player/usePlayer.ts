import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export const SKIP_SECONDS = 5;

export interface Player {
  readonly audioRef: RefObject<HTMLAudioElement | null>;
  readonly playing: boolean;
  readonly time: number;
  readonly duration: number | null;
  readonly failure: string | null;
  readonly toggle: () => void;
  readonly seekTo: (seconds: number) => void;
  readonly skip: (seconds: number) => void;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Un reproductor sobre un `<audio>`: estado de reproducción, posición y saltos. */
export function usePlayer(source: string | null): Player {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    setPlaying(false);
    setTime(0);
    setDuration(null);
    setFailure(null);
    const audio = audioRef.current;
    if (audio === null) {
      return undefined;
    }
    const onTime = () => setTime(audio.currentTime);
    const onMetadata = () => setDuration(Number.isFinite(audio.duration) ? audio.duration : null);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMetadata);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onPause);
    return () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMetadata);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onPause);
    };
  }, [source]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (audio === null) {
      return;
    }
    if (!audio.paused) {
      audio.pause();
      return;
    }
    setFailure(null);
    audio.play().catch((error: unknown) => setFailure(error instanceof Error ? error.message : String(error)));
  }, []);

  const seekTo = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (audio === null) {
      return;
    }
    const end = Number.isFinite(audio.duration) ? audio.duration : seconds;
    audio.currentTime = clamp(seconds, 0, end);
    setTime(audio.currentTime);
  }, []);

  const skip = useCallback((seconds: number) => seekTo((audioRef.current?.currentTime ?? 0) + seconds), [seekTo]);

  return { audioRef, playing, time, duration, failure, toggle, seekTo, skip };
}

/**
 * Que suene un solo reproductor a la vez: al empezar uno se pausan los de otro grupo
 * (`data-group`). Los dos lados de la escucha A/B comparten grupo y suenan juntos.
 */
export function useSinglePlayback(): void {
  useEffect(() => {
    const onPlay = (event: Event) => {
      const started = event.target;
      if (!(started instanceof HTMLMediaElement)) {
        return;
      }
      for (const media of document.querySelectorAll<HTMLMediaElement>("audio, video")) {
        if (media !== started && media.dataset.group !== started.dataset.group) {
          media.pause();
        }
      }
    };
    document.addEventListener("play", onPlay, true);
    return () => document.removeEventListener("play", onPlay, true);
  }, []);
}
