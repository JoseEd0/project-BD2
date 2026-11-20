import { describe, expect, it } from "vitest";

import { nextActiveIndex } from "../src/search/listNavigation";

describe("the preview's keyboard", () => {
  it("goes down, wraps back to the field and up from it", () => {
    expect(nextActiveIndex(-1, 3, 1)).toBe(0);
    expect(nextActiveIndex(2, 3, 1)).toBe(-1);
    expect(nextActiveIndex(-1, 3, -1)).toBe(2);
    expect(nextActiveIndex(0, 3, -1)).toBe(-1);
  });

  it("stays on the field with nothing to choose", () => {
    expect(nextActiveIndex(-1, 0, 1)).toBe(-1);
  });
});
