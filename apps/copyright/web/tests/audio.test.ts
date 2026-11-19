import { describe, expect, it } from "vitest";

import { reducePeaks, waveformPeaks } from "../src/audio/peaks";
import { alignedTime, drift, outsideSpan, spanEnd, spanStart } from "../src/listening/sync";
import { match } from "./detection";

describe("waveformPeaks", () => {
  it("gives nothing for an empty audio", () => {
    expect(waveformPeaks([], 10)).toHaveLength(0);
    expect(waveformPeaks([new Float32Array()], 10)).toHaveLength(0);
  });

  it("takes the loudest sample of any channel per bucket, normalised to the loudest bucket", () => {
    const left = Float32Array.from([0.1, -0.2, 0.05, 0.1]);
    const right = Float32Array.from([0, 0.1, -0.4, 0]);
    expect(Array.from(waveformPeaks([left, right], 2))).toEqual([0.5, 1]);
  });

  it("never asks for more buckets than samples", () => {
    expect(waveformPeaks([Float32Array.from([0.5])], 720)).toEqual(Float32Array.from([1]));
  });

  it("keeps a silent audio flat", () => {
    expect(Array.from(waveformPeaks([new Float32Array(8)], 4))).toEqual([0, 0, 0, 0]);
  });

  it("downsamples a long audio to the bucket count", () => {
    const samples = Float32Array.from({ length: 48_000 }, (_, index) => Math.sin(index / 10) * (index / 48_000));
    const peaks = waveformPeaks([samples], 300);
    expect(peaks).toHaveLength(300);
    expect(peaks[299]).toBeCloseTo(1, 2);
    expect(peaks[0]).toBeLessThan(0.01);
  });

  it("reduces peaks to fewer bars by their maximum", () => {
    expect(Array.from(reducePeaks(Float32Array.from([0.1, 0.9, 0.3, 0.2]), 2))).toEqual([Math.fround(0.9), Math.fround(0.3)]);
    const peaks = Float32Array.from([0.5, 1]);
    expect(reducePeaks(peaks, 10)).toBe(peaks);
  });
});

describe("a/b sync", () => {
  const copy = { ...match(1, [60, 87.5], 0.99), original: { start_seconds: 0, end_seconds: 27.5 } };

  it("starts each side at its own point of the span", () => {
    expect(spanStart(copy, "A")).toBe(60);
    expect(spanStart(copy, "B")).toBe(0);
    expect(spanEnd(copy, "B")).toBe(27.5);
  });

  it("aligns the other side to the same point of the span", () => {
    expect(alignedTime(copy, "A", 70)).toBe(10);
    expect(alignedTime(copy, "B", 10)).toBe(70);
  });

  it("loops once a side leaves its span", () => {
    expect(outsideSpan(copy, "A", 59.9)).toBe(true);
    expect(outsideSpan(copy, "A", 60)).toBe(false);
    expect(outsideSpan(copy, "A", 87.5)).toBe(true);
  });

  it("measures how far the muted side drifted", () => {
    expect(drift(copy, "A", 70, 10.2)).toBeCloseTo(0.2);
  });
});
