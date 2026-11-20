import { describe, expect, it } from "vitest";

import { formatBytes, formatClock, formatOffset, formatPercent, formatSpan } from "../src/analysis/format";
import { catalogStateOf, withoutSong } from "../src/analysis/catalog";
import { apiMessage, problemOf, unreachableProblem } from "../src/analysis/problem";
import { barlineTimes, rulerTicks } from "../src/analysis/ruler";
import { HIGHLIGHTER_INKS, layoutSpans, songLetter } from "../src/analysis/spans";
import { engineStages, serviceStages, stageLabel } from "../src/analysis/stages";
import { verdictOf } from "../src/analysis/verdict";
import { detection, match } from "./detection";

const NBSP = / /g;

describe("format", () => {
  it("writes clock times with tenths only when there are some", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(75.5)).toBe("1:15,5");
    expect(formatClock(600)).toBe("10:00");
    expect(formatSpan(2.5, 7.5)).toBe("0:02,5–0:07,5");
  });

  it("always shows the sign of an offset", () => {
    expect(formatOffset(60)).toBe("+1:00");
    expect(formatOffset(-2.5)).toBe("−0:02,5");
    expect(formatOffset(0)).toBe("0:00");
  });

  it("formats shares and sizes", () => {
    expect(formatPercent(0.997).replace(NBSP, " ")).toBe("100 %");
    expect(formatBytes(512 * 1024)).toBe("512 KB");
    expect(formatBytes(3.5 * 1024 * 1024)).toBe("3,5 MB");
  });
});

describe("layoutSpans", () => {
  it("places nothing for no matches", () => {
    expect(layoutSpans([], 60)).toEqual({ spans: [], lanes: 0 });
  });

  it("places one span as a share of the upload", () => {
    const { spans, lanes } = layoutSpans([match(7, [15, 30], 0.9)], 60);
    expect(lanes).toBe(1);
    expect(spans[0]).toMatchObject({ letter: "A", ink: 0, lane: 0, startShare: 0.25, widthShare: 0.25, weak: false });
  });

  it("gives every span of the same song its letter and ink, in order of appearance", () => {
    const { spans } = layoutSpans([match(7, [0, 10], 0.9), match(3, [20, 30], 0.8), match(7, [40, 50], 0.7)], 60);
    expect(spans.map((span) => span.letter)).toEqual(["A", "B", "A"]);
    expect(spans.map((span) => span.ink)).toEqual([0, 1, 0]);
  });

  it("stacks overlapping spans in lanes and reuses a lane once it is free", () => {
    const { spans, lanes } = layoutSpans([match(1, [0, 20], 0.9), match(2, [10, 30], 0.8), match(3, [25, 40], 0.7)], 60);
    expect(spans.map((span) => span.lane)).toEqual([0, 1, 0]);
    expect(lanes).toBe(2);
  });

  it("clamps spans to the upload and marks the weak ones", () => {
    const [span] = layoutSpans([match(1, [55, 65], 0.3)], 60).spans;
    expect(span?.startShare).toBeCloseTo(55 / 60);
    expect(span?.widthShare).toBeCloseTo(5 / 60);
    expect(span?.weak).toBe(true);
  });

  it("cycles inks past the last highlighter and letters past z", () => {
    const many = Array.from({ length: HIGHLIGHTER_INKS + 1 }, (_, index) => match(index, [index, index + 1], 0.9));
    expect(layoutSpans(many, 60).spans.at(-1)?.ink).toBe(0);
    expect(songLetter(25)).toBe("Z");
    expect(songLetter(26)).toBe("A2");
  });
});

describe("verdictOf", () => {
  it("names fragments and songs of a likely copy", () => {
    const verdict = verdictOf(detection([match(1, [0, 10], 0.9), match(2, [20, 30], 0.6), match(3, [40, 50], 0.2)]));
    expect(verdict.tone).toBe("copy");
    expect(verdict.headline).toBe("Posible uso no autorizado: 2 fragmentos de 2 canciones");
  });

  it("counts two fragments of one song in the singular", () => {
    expect(verdictOf(detection([match(1, [0, 10], 0.9), match(1, [20, 30], 0.8)])).headline).toBe(
      "Posible uso no autorizado: 2 fragmentos de 1 canción",
    );
  });

  it("keeps weak matches below the threshold out of the copy count", () => {
    const verdict = verdictOf(detection([match(1, [0, 10], 0.3)]));
    expect(verdict.tone).toBe("weak");
    expect(verdict.headline).toBe("No encontramos copias claras");
    expect(verdict.detail.replace(NBSP, " ")).toBe("1 coincidencia queda bajo el umbral de 45 %: escúchalas para descartarlas.");
  });

  it("says plainly when nothing matches, and when the audio is silent", () => {
    expect(verdictOf(detection([])).headline).toBe("No encontramos coincidencias con el catálogo");
    const silent = verdictOf(detection([], { windows: 3, silent_windows: 3 }));
    expect(silent).toMatchObject({ tone: "silent", headline: "El audio está en silencio" });
  });
});

describe("problemOf", () => {
  it("rejects with the api's reason, capitalised and closed", () => {
    const problem = problemOf(415, { error: "el archivo no es un audio MP3, WAV, FLAC ni OGG", kind: "UnsupportedAudioError" });
    expect(problem).toEqual({
      kind: "rejected",
      title: "Ese archivo no es un audio que podamos leer",
      detail: "El archivo no es un audio MP3, WAV, FLAC ni OGG.",
      status: 415,
    });
  });

  it("explains a 413 from nginx, which has no json reason", () => {
    expect(problemOf(413, "<html>413 Request Entity Too Large</html>")).toMatchObject({
      kind: "rejected",
      title: "El audio es demasiado grande",
    });
  });

  it("maps a short or damaged audio to its reason", () => {
    expect(problemOf(400, { error: "el audio dura 0.40 s y hace falta al menos 1 s" }).detail).toBe(
      "El audio dura 0.40 s y hace falta al menos 1 s.",
    );
  });

  it("tells an unavailable service from a failing one", () => {
    expect(problemOf(502, "Bad Gateway").kind).toBe("unavailable");
    expect(unreachableProblem()).toMatchObject({ kind: "unavailable", status: null });
    expect(problemOf(500, { error: "el servicio falló." })).toMatchObject({ kind: "failed", detail: "El servicio falló." });
    expect(problemOf(429, {}).title).toBe("Demasiadas subidas seguidas");
  });

  it("tells a catalog still loading or busy apart from a bad upload", () => {
    const loading = problemOf(400, { error: "La tabla 'cr_segmentos' no tiene un índice HNSW sobre la columna 'ruta'" });
    expect(loading).toMatchObject({ kind: "loading", title: "El catálogo se está cargando" });
    expect(loading.detail).not.toContain("cr_segmentos");
    expect(problemOf(400, { error: "el catálogo lleva más de 5 s ocupado por otra transacción" })).toMatchObject({
      kind: "busy",
      title: "El catálogo está ocupado",
    });
  });

  it("ignores bodies without a usable message", () => {
    expect(apiMessage(null)).toBeNull();
    expect(apiMessage({ error: "  " })).toBeNull();
    expect(apiMessage({ detail: "x" })).toBeNull();
  });
});

describe("stages", () => {
  const header =
    "probe;dur=4, loudness;dur=16, segments;dur=60, decode;dur=20, extraction;dur=30, search;dur=1, voting;dur=1, songs;dur=9, total;dur=100";

  it("leaves the engine stages out of the service total, where segments already counts them", () => {
    const timings = serviceStages(header);
    expect(timings?.totalMs).toBe(100);
    expect(timings?.rows.map((row) => row.key)).toEqual(["probe", "loudness", "segments", "voting", "songs", "unattributed"]);
    expect(timings?.rows.at(-1)?.durationMs).toBe(10);
  });

  it("reads the engine stages from the body", () => {
    expect(engineStages({ decode_ms: 1, extraction_ms: 2, search_ms: 3 }).map((stage) => stage.durationMs)).toEqual([1, 2, 3]);
    expect(engineStages(null)).toEqual([]);
    expect(serviceStages(null)).toBeNull();
    expect(stageLabel("voting")).toBe("Votación por desfase");
    expect(stageLabel("otra")).toBe("otra");
  });
});

describe("ruler", () => {
  it("picks the finest round step that fits", () => {
    expect(rulerTicks(27.5, 6)).toEqual([0, 5, 10, 15, 20, 25]);
    expect(rulerTicks(180, 5)).toEqual([0, 60, 120, 180]);
    expect(rulerTicks(0, 5)).toEqual([0]);
  });

  it("draws a barline per hop, thinned when too many", () => {
    expect(barlineTimes(10, 2.5, 100)).toEqual([2.5, 5, 7.5]);
    const thinned = barlineTimes(600, 2.5, 96);
    expect(thinned.length).toBeLessThanOrEqual(96);
    expect(thinned.slice(0, 2)).toEqual([7.5, 15]);
    expect(barlineTimes(10, 0, 10)).toEqual([]);
  });
});

describe("catalog", () => {
  const summary = { songs: 10, segments: 100, window_seconds: 5, hop_seconds: 2.5, segments_indexed: true, genres: [] };

  it("is ready only with songs, segments and their index", () => {
    expect(catalogStateOf(summary)).toBe("ready");
    expect(catalogStateOf({ ...summary, segments_indexed: false })).toBe("indexing");
    expect(catalogStateOf({ ...summary, songs: 0, segments: 0 })).toBe("empty");
  });

  it("drops the matches of the chosen song with itself", () => {
    const own = match(7, [0, 30], 1);
    const other = match(8, [5, 15], 0.2);
    const result = withoutSong(detection([own, other]), 7);
    expect(result.matches).toEqual([other]);
    expect(result.detected).toBe(false);
  });
});
