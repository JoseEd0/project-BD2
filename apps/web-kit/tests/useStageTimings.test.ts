import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UNATTRIBUTED_KEY, useStageTimings } from "../src/index.ts";

describe("useStageTimings", () => {
  it("lists each stage with its time and share, plus the unattributed rest", () => {
    const { result } = renderHook(() => useStageTimings("parse;dur=1, exec;dur=2, total;dur=4"));
    expect(result.current?.totalMs).toBe(4);
    expect(result.current?.rows.map((row) => [row.key, row.durationMs, row.share, row.unattributed])).toEqual([
      ["parse", 1, 0.25, false],
      ["exec", 2, 0.5, false],
      [UNATTRIBUTED_KEY, 1, 0.25, true],
    ]);
  });

  it("keeps the description an app may show for a stage it does not know", () => {
    const { result } = renderHook(() => useStageTimings('rerank;dur=3;desc="Reordenar", embed;dur=1'));
    expect(result.current?.rows.map((row) => row.description)).toEqual(["Reordenar", null]);
  });

  it("gives a lone total without rows to split", () => {
    const { result } = renderHook(() => useStageTimings("total;dur=1.150"));
    expect(result.current).toEqual({ totalMs: 1.15, rows: [] });
  });

  it("is null when the header is missing or carries no durations", () => {
    const { result, rerender } = renderHook(({ header }) => useStageTimings(header), {
      initialProps: { header: null as string | null },
    });
    expect(result.current).toBeNull();
    rerender({ header: "garbage;;;, ,=" });
    expect(result.current).toBeNull();
  });

  it("keeps the same result while the header does not change", () => {
    const { result, rerender } = renderHook(() => useStageTimings("exec;dur=2"));
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
