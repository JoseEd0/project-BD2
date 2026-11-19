import { waveformPeaks } from "./peaks";

/** Barras de la forma de onda: a 1 100 px quedan de 3 px con su hueco, como en un editor de audio. */
export const WAVEFORM_BUCKETS = 320;

export interface Waveform {
  readonly peaks: Float32Array;
  readonly durationSeconds: number;
}

/**
 * Decodifica el archivo en el navegador (Web Audio) y lo reduce a picos. El audio no sale de
 * aquí: lo que se sube al API es el archivo tal cual.
 *
 * Throws: si el navegador no sabe decodificarlo (`EncodingError`).
 */
export async function decodeWaveform(file: Blob): Promise<Waveform> {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await file.arrayBuffer());
    const channels = Array.from({ length: buffer.numberOfChannels }, (_, channel) => buffer.getChannelData(channel));
    return { peaks: waveformPeaks(channels, WAVEFORM_BUCKETS), durationSeconds: buffer.duration };
  } finally {
    void context.close();
  }
}
