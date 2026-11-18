import { describe, expect, it } from "vitest";

import { parseServerTiming, stageBreakdown } from "../src/timing/serverTiming";

const ENGINE_HEADER = "parse;dur=0.412, plan;dur=0.130, exec;dur=3.201, txn;dur=0.050, serialize;dur=0.200, total;dur=4.500";

describe("parseServerTiming", () => {
  it("reads the stages the engine emits, in order", () => {
    expect(parseServerTiming(ENGINE_HEADER).map((metric) => [metric.name, metric.durationMs])).toEqual([
      ["parse", 0.412],
      ["plan", 0.13],
      ["exec", 3.201],
      ["txn", 0.05],
      ["serialize", 0.2],
      ["total", 4.5],
    ]);
  });

  it("returns nothing for a missing or empty header", () => {
    expect(parseServerTiming(null)).toEqual([]);
    expect(parseServerTiming(undefined)).toEqual([]);
    expect(parseServerTiming("")).toEqual([]);
    expect(parseServerTiming(" , ; ,")).toEqual([]);
  });

  it("keeps commas and semicolons inside a quoted description", () => {
    const [metric] = parseServerTiming('db;desc="read, then; sort \\"fast\\"";dur=2');
    expect(metric).toEqual({ name: "db", durationMs: 2, description: 'read, then; sort "fast"' });
  });

  it("accepts optional whitespace and parameter names in any case", () => {
    expect(parseServerTiming("  cache ; DUR = 1.5 ; Desc=hit ")).toEqual([
      { name: "cache", durationMs: 1.5, description: "hit" },
    ]);
  });

  it("drops entries whose name is not a token", () => {
    expect(parseServerTiming('"quoted";dur=1, bad name;dur=2, ok;dur=3').map((metric) => metric.name)).toEqual(["ok"]);
  });

  it("keeps a metric without a usable duration but with no duration", () => {
    expect(parseServerTiming("a;dur=-1, b;dur=abc, c;dur, d;dur=Infinity, e").map((metric) => metric.durationMs)).toEqual([
      null,
      null,
      null,
      null,
      null,
    ]);
  });

  it("keeps the first occurrence of a repeated metric and of a repeated parameter", () => {
    expect(parseServerTiming("exec;dur=1;dur=9, exec;dur=5")).toEqual([
      { name: "exec", durationMs: 1, description: null },
    ]);
  });

  it("ignores an unterminated quoted value", () => {
    expect(parseServerTiming('x;desc="open;dur=3')).toEqual([{ name: "x", durationMs: null, description: null }]);
  });
});

describe("stageBreakdown", () => {
  it("leaves what the total measures beyond the stages as unattributed", () => {
    const breakdown = stageBreakdown(parseServerTiming(ENGINE_HEADER));
    expect(breakdown?.totalMs).toBe(4.5);
    expect(breakdown?.stages.map((stage) => stage.name)).toEqual(["parse", "plan", "exec", "txn", "serialize"]);
    expect(breakdown?.unattributedMs).toBeCloseTo(0.507);
    expect(breakdown?.stages[2]?.share).toBeCloseTo(3.201 / 4.5);
  });

  it("uses the sum of the stages when there is no total", () => {
    const breakdown = stageBreakdown(parseServerTiming("parse;dur=1, exec;dur=3"));
    expect(breakdown).toMatchObject({ totalMs: 4, unattributedMs: 0 });
    expect(breakdown?.stages.map((stage) => stage.share)).toEqual([0.25, 0.75]);
  });

  it("never reports a total smaller than its stages", () => {
    expect(stageBreakdown(parseServerTiming("exec;dur=5, total;dur=2"))).toMatchObject({ totalMs: 5, unattributedMs: 0 });
  });

  it("shows a lone total as entirely unattributed", () => {
    expect(stageBreakdown(parseServerTiming("total;dur=0.8"))).toEqual({ stages: [], totalMs: 0.8, unattributedMs: 0.8 });
  });

  it("gives zero shares when every duration is zero", () => {
    expect(stageBreakdown(parseServerTiming("exec;dur=0"))?.stages[0]?.share).toBe(0);
  });

  it("is null when no metric carries a duration", () => {
    expect(stageBreakdown(parseServerTiming("cache, miss;desc=cold"))).toBeNull();
    expect(stageBreakdown([])).toBeNull();
  });
});
