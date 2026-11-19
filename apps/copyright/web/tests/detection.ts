import type { DetectedMatch, Detection } from "../src/api/types";

export function match(songId: number, upload: [number, number], score: number, aboveThreshold = score >= 0.45): DetectedMatch {
  return {
    song: { id: songId, title: `Canción ${songId}`, artist: "Muestra" },
    upload: { start_seconds: upload[0], end_seconds: upload[1] },
    original: { start_seconds: upload[0] + 5, end_seconds: upload[1] + 5 },
    offset_seconds: -5,
    windows: 4,
    mean_similarity: score,
    coverage: 1,
    score,
    above_threshold: aboveThreshold,
  };
}

export function detection(matches: DetectedMatch[], overrides: Partial<Detection> = {}): Detection {
  return {
    format: "MP3",
    duration_seconds: 60,
    sample_rate: 44_100,
    channels: 2,
    window_seconds: 5,
    hop_seconds: 2.5,
    windows: 23,
    silent_windows: 0,
    threshold: 0.45,
    detected: matches.some((item) => item.above_threshold),
    matches,
    engine_timings: { decode_ms: 10, extraction_ms: 20, search_ms: 1 },
    ...overrides,
  };
}
