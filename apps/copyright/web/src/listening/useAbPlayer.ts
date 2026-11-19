import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import type { DetectedMatch } from "../api/types";
import { alignedTime, DRIFT_TOLERANCE_SECONDS, drift, otherSide, outsideSpan, spanStart, type Side } from "./sync";

const HAVE_METADATA = 1;

export interface AbPlayer {
  readonly uploadRef: RefObject<HTMLAudioElement | null>;
  readonly originalRef: RefObject<HTMLAudioElement | null>;
  readonly side: Side;
  readonly playing: boolean;
  /** Dónde va cada audio, en segundos: los cabezales de los dos pentagramas. */
  readonly uploadTime: number;
  readonly originalTime: number;
  readonly failure: string | null;
  readonly choose: (side: Side) => void;
  readonly toggle: () => void;
  readonly playPause: () => void;
}

function seek(audio: HTMLAudioElement, time: number): void {
  if (audio.readyState >= HAVE_METADATA) {
    audio.currentTime = time;
    return;
  }
  audio.addEventListener("loadedmetadata", () => (audio.currentTime = time), { once: true });
}

/**
 * La escucha A/B de una coincidencia: la subida (A) y el original (B) suenan a la vez desde
 * el mismo punto del tramo y solo se oye uno; alternar no corta ni salta, como una caja A/B.
 * El tramo se repite en bucle y el lado mudo se realinea si se separa.
 */
export function useAbPlayer(match: DetectedMatch | null): AbPlayer {
  const uploadRef = useRef<HTMLAudioElement | null>(null);
  const originalRef = useRef<HTMLAudioElement | null>(null);
  const [side, setSide] = useState<Side>("A");
  const [playing, setPlaying] = useState(false);
  const [times, setTimes] = useState({ upload: 0, original: 0 });
  const [failure, setFailure] = useState<string | null>(null);

  const audioOf = useCallback((which: Side) => (which === "A" ? uploadRef.current : originalRef.current), []);

  const rewind = useCallback(() => {
    if (match === null) {
      return;
    }
    for (const which of ["A", "B"] as const) {
      const audio = audioOf(which);
      if (audio !== null) {
        seek(audio, spanStart(match, which));
      }
    }
    setTimes({ upload: match.upload.start_seconds, original: match.original.start_seconds });
  }, [audioOf, match]);

  const pause = useCallback(() => {
    uploadRef.current?.pause();
    originalRef.current?.pause();
    setPlaying(false);
  }, []);

  useEffect(() => {
    pause();
    setFailure(null);
    rewind();
  }, [pause, rewind]);

  const applyMute = useCallback((heard: Side) => {
    const upload = uploadRef.current;
    const original = originalRef.current;
    if (upload !== null && original !== null) {
      upload.muted = heard !== "A";
      original.muted = heard !== "B";
    }
  }, []);

  const choose = useCallback(
    (next: Side) => {
      const leader = audioOf(side);
      const follower = audioOf(next);
      if (match !== null && leader !== null && follower !== null && next !== side) {
        follower.currentTime = alignedTime(match, side, leader.currentTime);
      }
      applyMute(next);
      setSide(next);
    },
    [applyMute, audioOf, match, side],
  );

  const toggle = useCallback(() => choose(otherSide(side)), [choose, side]);

  const playPause = useCallback(() => {
    const upload = uploadRef.current;
    const original = originalRef.current;
    if (match === null || upload === null || original === null) {
      return;
    }
    if (playing) {
      pause();
      return;
    }
    if (outsideSpan(match, "A", upload.currentTime)) {
      rewind();
    }
    applyMute(side);
    setFailure(null);
    setPlaying(true);
    Promise.all([upload.play(), original.play()]).catch((error: unknown) => {
      pause();
      setFailure(error instanceof Error ? error.message : String(error));
    });
  }, [applyMute, match, pause, playing, rewind, side]);

  useEffect(() => {
    if (!playing || match === null) {
      return undefined;
    }
    let frame = requestAnimationFrame(function follow() {
      const leader = audioOf(side);
      const follower = audioOf(otherSide(side));
      if (leader !== null && follower !== null) {
        if (outsideSpan(match, side, leader.currentTime)) {
          rewind();
        } else if (drift(match, side, leader.currentTime, follower.currentTime) > DRIFT_TOLERANCE_SECONDS) {
          follower.currentTime = alignedTime(match, side, leader.currentTime);
        }
        setTimes({ upload: uploadRef.current?.currentTime ?? 0, original: originalRef.current?.currentTime ?? 0 });
      }
      frame = requestAnimationFrame(follow);
    });
    return () => cancelAnimationFrame(frame);
  }, [audioOf, match, playing, rewind, side]);

  return { uploadRef, originalRef, side, playing, uploadTime: times.upload, originalTime: times.original, failure, choose, toggle, playPause };
}
