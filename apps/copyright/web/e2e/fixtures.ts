import type { BrowserContext, Route } from "@playwright/test";

/**
 * Lo que el API respondería con el catálogo de muestra (`songcheck.sample`): melodías de
 * notas al azar. El remix son 15 s de la melodía 2 seguidos de 12,5 s de la 3, como el que
 * sube el humo del compose contra el API real; aquí el API se simula y el audio es de verdad,
 * para que el navegador dibuje su forma de onda y lo reproduzca.
 */
const SAMPLE_RATE = 16_000;
const NOTE_SECONDS = 0.25;
const LOWEST_HZ = 220;
const SEMITONES = 36;
const AMPLITUDE = 8_000;

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
    return state / 2 ** 32;
  };
}

function melody(seconds: number, seed: number): Int16Array {
  const random = seededRandom(seed);
  const frames = Math.round(seconds * SAMPLE_RATE);
  const noteFrames = Math.round(NOTE_SECONDS * SAMPLE_RATE);
  const samples = new Int16Array(frames);
  let pitch = LOWEST_HZ;
  let velocity = 1;
  for (let index = 0; index < frames; index += 1) {
    const position = index % noteFrames;
    if (position === 0) {
      pitch = LOWEST_HZ * 2 ** (Math.floor(random() * SEMITONES) / 12);
      velocity = 0.3 + 0.7 * random();
    }
    const decay = Math.min(1, position / 160) * Math.exp((-3 * position) / noteFrames);
    samples[index] = Math.round(AMPLITUDE * velocity * decay * Math.sin((2 * Math.PI * pitch * index) / SAMPLE_RATE));
  }
  return samples;
}

function wav(samples: Int16Array): Buffer {
  const header = Buffer.alloc(44);
  const dataBytes = samples.length * 2;
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write("WAVEfmt ", 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(SAMPLE_RATE * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(dataBytes, 40);
  return Buffer.concat([header, Buffer.from(samples.buffer)]);
}

const SONG_SECONDS = 30;
const songs = new Map([
  [2, { id: 2, title: "Melodía 2", artist: "Muestra", genre: "Folk", duration_seconds: SONG_SECONDS, segments: [] }],
  [3, { id: 3, title: "Melodía 3", artist: "Muestra", genre: "Hip-Hop", duration_seconds: SONG_SECONDS, segments: [] }],
]);
const tracks = new Map([...songs.keys()].map((id) => [id, melody(SONG_SECONDS, id)]));

export function remixFile(): { name: string; mimeType: string; buffer: Buffer } {
  const second = tracks.get(2)!.subarray(10 * SAMPLE_RATE, 25 * SAMPLE_RATE);
  const third = tracks.get(3)!.subarray(5 * SAMPLE_RATE, 17.5 * SAMPLE_RATE);
  const remix = new Int16Array(second.length + third.length);
  remix.set(second);
  remix.set(third, second.length);
  return { name: "remix de la muestra.wav", mimeType: "audio/wav", buffer: wav(remix) };
}

export const REMIX_DETECTION = {
  format: "WAV",
  duration_seconds: 27.5,
  sample_rate: SAMPLE_RATE,
  channels: 1,
  window_seconds: 5,
  hop_seconds: 2.5,
  windows: 10,
  silent_windows: 0,
  threshold: 0.45,
  detected: true,
  matches: [
    {
      song: { id: 2, title: "Melodía 2", artist: "Muestra" },
      upload: { start_seconds: 0, end_seconds: 15 },
      original: { start_seconds: 10, end_seconds: 25 },
      offset_seconds: -10,
      windows: 5,
      mean_similarity: 0.991,
      coverage: 1,
      score: 0.991,
      above_threshold: true,
    },
    {
      song: { id: 3, title: "Melodía 3", artist: "Muestra" },
      upload: { start_seconds: 15, end_seconds: 27.5 },
      original: { start_seconds: 5, end_seconds: 17.5 },
      offset_seconds: 10,
      windows: 4,
      mean_similarity: 0.962,
      coverage: 0.8,
      score: 0.77,
      above_threshold: true,
    },
  ],
  engine_timings: { decode_ms: 31.2, extraction_ms: 52.8, search_ms: 0.9 },
};

export const DETECTION_TIMING =
  "probe;dur=4.1, loudness;dur=18.6, segments;dur=92.3, decode;dur=31.2, extraction;dur=52.8, search;dur=0.9, voting;dur=0.4, songs;dur=6.2, total;dur=128.9";

type Responder = (route: Route) => Promise<void>;

export function respondWith(status: number, body: unknown, headers: Record<string, string> = {}): Responder {
  return (route) => route.fulfill({ status, json: body, headers });
}

export const READY_CATALOG = {
  songs: songs.size,
  segments: songs.size * 11,
  window_seconds: 5,
  hop_seconds: 2.5,
  segments_indexed: true,
  genres: [],
};

/** Simula el API de la app: salud, catálogo, canciones, su audio y `POST /detections`. */
export async function mockApi(context: BrowserContext, detections: Responder, catalog: object = READY_CATALOG): Promise<void> {
  await context.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", version: "0.1.0" } }));
  await context.route("**/api/catalog", (route) => route.fulfill({ json: catalog }));
  await context.route(/\/api\/songs(\?.*)?$/, (route) =>
    route.fulfill({ json: { songs: [...songs.values()].map(({ segments: _segments, ...song }) => song) } }),
  );
  await context.route("**/api/detections", detections);
  await context.route(/\/api\/songs\/(\d+)\/audio$/, (route) => {
    const id = Number(new URL(route.request().url()).pathname.split("/").at(-2));
    const track = tracks.get(id);
    return track === undefined
      ? route.fulfill({ status: 404, json: { error: "no hay una canción", kind: "ResourceNotFoundError" } })
      : route.fulfill({ status: 200, body: wav(track), headers: { "Content-Type": "audio/wav", "Accept-Ranges": "bytes" } });
  });
  await context.route(/\/api\/songs\/(\d+)$/, (route) => {
    const id = Number(new URL(route.request().url()).pathname.split("/").at(-1));
    const song = songs.get(id);
    return song === undefined ? route.fulfill({ status: 404, json: {} }) : route.fulfill({ json: song });
  });
}
