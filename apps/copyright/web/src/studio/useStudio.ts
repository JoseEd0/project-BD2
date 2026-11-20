import { useCallback, useEffect, useRef, useState } from "react";

import type { Problem } from "../analysis/problem";
import { CatalogProblem, songFile } from "../api/catalog";
import { DetectionProblem, uploadForDetection, type DetectionResult } from "../api/detections";
import type { Song } from "../api/types";
import { decodeWaveform, type Waveform } from "../audio/decodeWaveform";

export type Phase =
  | { readonly kind: "idle"; readonly notice: string | null }
  | { readonly kind: "fetching"; readonly song: Song }
  | { readonly kind: "uploading"; readonly progress: number | null }
  | { readonly kind: "analyzing" }
  | { readonly kind: "done"; readonly result: DetectionResult }
  | { readonly kind: "problem"; readonly problem: Problem };

export type WaveformState =
  | { readonly status: "decoding" }
  | { readonly status: "ready"; readonly waveform: Waveform }
  | { readonly status: "unavailable" };

export interface Studio {
  readonly file: File | null;
  /** La canción del catálogo que se analiza, o `null` si el audio es una subida. */
  readonly source: Song | null;
  /** El archivo como `blob:` para escucharlo (lado A). */
  readonly audioUrl: string | null;
  readonly phase: Phase;
  readonly waveform: WaveformState;
  readonly analyse: (file: File) => void;
  readonly analyseSong: (song: Song) => void;
  readonly cancel: () => void;
  readonly retry: () => void;
  readonly reset: () => void;
}

const IDLE: Phase = { kind: "idle", notice: null };

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function useObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (file === null) {
      setUrl(null);
      return undefined;
    }
    const created = URL.createObjectURL(file);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [file]);
  return url;
}

function useWaveform(file: File | null): WaveformState {
  const [state, setState] = useState<WaveformState>({ status: "decoding" });
  useEffect(() => {
    if (file === null) {
      return undefined;
    }
    let current = true;
    setState({ status: "decoding" });
    decodeWaveform(file).then(
      (waveform) => current && setState({ status: "ready", waveform }),
      () => current && setState({ status: "unavailable" }),
    );
    return () => {
      current = false;
    };
  }, [file]);
  return state;
}

/**
 * El flujo del estudio: un archivo elegido se decodifica en el navegador (forma de onda) y a
 * la vez se sube a `POST /detections`, con progreso y cancelación.
 */
export function useStudio(): Studio {
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<Song | null>(null);
  const [phase, setPhase] = useState<Phase>(IDLE);
  const upload = useRef<AbortController | null>(null);
  const audioUrl = useObjectUrl(file);
  const waveform = useWaveform(file);

  const start = useCallback((chosen: File) => {
    upload.current?.abort();
    const controller = new AbortController();
    upload.current = controller;
    setPhase({ kind: "uploading", progress: 0 });
    uploadForDetection(chosen, {
      signal: controller.signal,
      onProgress: (progress) => setPhase({ kind: "uploading", progress }),
      onUploaded: () => setPhase({ kind: "analyzing" }),
    }).then(
      (result) => setPhase({ kind: "done", result }),
      (error: unknown) => {
        if (isAbort(error)) {
          return;
        }
        if (error instanceof DetectionProblem) {
          setPhase({ kind: "problem", problem: error.problem });
          return;
        }
        throw error;
      },
    );
  }, []);

  const analyse = useCallback(
    (chosen: File) => {
      setSource(null);
      setFile(chosen);
      start(chosen);
    },
    [start],
  );

  const analyseSong = useCallback(
    (song: Song) => {
      upload.current?.abort();
      const controller = new AbortController();
      upload.current = controller;
      setSource(song);
      setFile(null);
      setPhase({ kind: "fetching", song });
      songFile(song, controller.signal).then(
        (chosen) => {
          setFile(chosen);
          start(chosen);
        },
        (error: unknown) => {
          if (isAbort(error)) {
            return;
          }
          if (error instanceof CatalogProblem) {
            setPhase({ kind: "problem", problem: error.problem });
            return;
          }
          throw error;
        },
      );
    },
    [start],
  );

  const cancel = useCallback(() => {
    upload.current?.abort();
    upload.current = null;
    setPhase({ kind: "idle", notice: file === null ? null : `Cancelaste el análisis de «${file.name}».` });
    setFile(null);
    setSource(null);
  }, [file]);

  const retry = useCallback(() => {
    if (file !== null) {
      start(file);
      return;
    }
    if (source !== null) {
      analyseSong(source);
    }
  }, [analyseSong, file, source, start]);

  const reset = useCallback(() => {
    upload.current?.abort();
    upload.current = null;
    setFile(null);
    setSource(null);
    setPhase(IDLE);
  }, []);

  useEffect(() => () => upload.current?.abort(), []);

  return { file, source, audioUrl, phase, waveform, analyse, analyseSong, cancel, retry, reset };
}
