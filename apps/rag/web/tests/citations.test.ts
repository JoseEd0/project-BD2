import { describe, expect, it } from "vitest";

import { citedNumbers, isValidCitation, segmentAnswer } from "../src/research/citations";

describe("segmentAnswer", () => {
  it("links the three citation forms the service checks", () => {
    expect(segmentAnswer("A [2]. B [1][3] y C [1, 3].")).toEqual([
      { kind: "text", text: "A " },
      { kind: "citation", numbers: [2], raw: "[2]" },
      { kind: "text", text: ". B " },
      { kind: "citation", numbers: [1], raw: "[1]" },
      { kind: "citation", numbers: [3], raw: "[3]" },
      { kind: "text", text: " y C " },
      { kind: "citation", numbers: [1, 3], raw: "[1, 3]" },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves brackets that are not citations as text", () => {
    expect(segmentAnswer("un arreglo [a] y [] y [1-2]")).toEqual([{ kind: "text", text: "un arreglo [a] y [] y [1-2]" }]);
  });

  it("keeps a half-streamed citation as text until it closes", () => {
    expect(segmentAnswer("los nodos [1")).toEqual([{ kind: "text", text: "los nodos [1" }]);
  });
});

describe("citations", () => {
  it("accepts only the numbers of the chunks that were sent", () => {
    expect([0, 1, 8, 9, 1.5].map((n) => isValidCitation(n, 8))).toEqual([false, true, true, false, false]);
  });

  it("collects the valid cited numbers once", () => {
    expect([...citedNumbers("a [2] b [2][9] c [1, 2]", 8)]).toEqual([2, 1]);
  });
});

