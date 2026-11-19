import { describe, expect, it } from "vitest";

import { locatePassage, normalizeForMatch } from "../src/reader/passage";

const PAGE = [
  "2.4 Learned Indexing",
  "The advent of fast and accurate machine learning tech-",
  "niques inspired the design of a new type of index struc-",
  "ture, called learned index [37]. The main idea is to",
  "learn a cumulative distribution function (CDF) of the keys",
  "and define a Recursive Model Index that replaces the inner",
  "nodes of a B+-tree by a hierarchy of models.",
  "3 THE BS-TREE",
];

const PASSAGE =
  "The advent of fast and accurate machine learning techniques inspired the design of a new type of index structure, called learned index [37]. " +
  "The main idea is to learn a cumulative distribution function (CDF) of the keys and define a Recursive Model Index that replaces the inner nodes of a B+-tree by a hierarchy of models.";

describe("locatePassage", () => {
  it("finds the text runs of a cleaned passage despite hyphenation and spacing", () => {
    expect(locatePassage(PAGE, PASSAGE)).toEqual({ first: 1, last: 6 });
  });

  it("matches ligatures and accents through Unicode normalization", () => {
    expect(normalizeForMatch("ﬁnding Índice")).toBe("findingindice");
    expect(locatePassage(["the ﬁrst eﬃcient", "structure of all"], "The first efficient structure of all, a long enough passage")).toEqual({
      first: 0,
      last: 1,
    });
  });

  it("marks to the bottom of the page when the passage continues on the next one", () => {
    const continued = `${PASSAGE.slice(0, 160)} and then this sentence only exists on the following page of the paper.`;
    expect(locatePassage(PAGE, continued, { startsBefore: false, endsAfter: true })).toEqual({ first: 1, last: PAGE.length - 1 });
  });

  it("marks from the top when the passage began on the previous page", () => {
    const started = `Text that was printed on the previous page before the break. ${PASSAGE.slice(150)}`;
    expect(locatePassage(PAGE, started, { startsBefore: true, endsAfter: false })).toEqual({ first: 0, last: 6 });
  });

  it("finds a passage whose first words the extraction dropped", () => {
    expect(locatePassage(PAGE, `Figure 3: a caption the cleaning moved elsewhere. ${PASSAGE}`)).toEqual({ first: 1, last: 6 });
  });

  it("returns null for a passage that is not on the page, or an empty page", () => {
    expect(locatePassage(PAGE, "Nothing in this sentence appears anywhere on the page at all, not even close.")).toBeNull();
    expect(locatePassage([], PASSAGE)).toBeNull();
  });
});
