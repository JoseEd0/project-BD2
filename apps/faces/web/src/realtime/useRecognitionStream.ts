import { useEffect, useRef, useState, type RefObject } from "react";

import { captureJpeg } from "../camera/capture";
import { API_BASE_URL, STREAM_JPEG, streamUrl } from "../config";
import type { SchedulerOptions } from "./scheduler";
import { RecognitionStream, type CapturedFrame, type FrameAnswer, type StreamState } from "./stream";

export interface RecognitionStreamOptions {
  readonly video: RefObject<HTMLVideoElement | null>;
  /** Solo se conecta con la cámara viva y la pantalla en uso. */
  readonly active: boolean;
  readonly scheduler: SchedulerOptions;
}

export interface RecognitionStreamView {
  readonly state: StreamState;
  readonly answer: FrameAnswer | null;
  resume(): void;
}

const INITIAL_STATE: StreamState = { phase: "stopped", attempt: 0, retryInMs: null, slow: false, roundTripMs: null };
const RESUME_EVENTS = ["pointerdown", "keydown"] as const;

async function captureFrame(video: HTMLVideoElement | null, canvas: HTMLCanvasElement): Promise<CapturedFrame | null> {
  if (video === null) {
    return null;
  }
  const jpeg = await captureJpeg(video, canvas, STREAM_JPEG);
  if (jpeg === null) {
    return null;
  }
  return { bytes: new Uint8Array(await jpeg.blob.arrayBuffer()), size: jpeg.size };
}

/** El reconocimiento en vivo atado al ciclo de la pantalla: conecta, pausa y retoma. */
export function useRecognitionStream({ video, active, scheduler }: RecognitionStreamOptions): RecognitionStreamView {
  const [state, setState] = useState<StreamState>(INITIAL_STATE);
  const [answer, setAnswer] = useState<FrameAnswer | null>(null);
  const stream = useRef<RecognitionStream | null>(null);

  useEffect(() => {
    const canvas = document.createElement("canvas");
    const created = new RecognitionStream({
      url: streamUrl(API_BASE_URL, window.location),
      scheduler,
      capture: () => captureFrame(video.current, canvas),
      onState: setState,
      onAnswer: setAnswer,
    });
    stream.current = created;
    return () => {
      created.stop();
      stream.current = null;
    };
  }, [video, scheduler]);

  useEffect(() => {
    if (active) {
      stream.current?.start();
      return;
    }
    stream.current?.stop();
    setAnswer(null);
  }, [active]);

  useEffect(() => {
    if (state.phase !== "idle") {
      return;
    }
    const resume = () => stream.current?.resume();
    RESUME_EVENTS.forEach((name) => window.addEventListener(name, resume, { once: true }));
    return () => RESUME_EVENTS.forEach((name) => window.removeEventListener(name, resume));
  }, [state.phase]);

  return { state, answer, resume: () => stream.current?.resume() };
}
