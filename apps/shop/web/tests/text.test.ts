import { describe, expect, it } from "vitest";

import { customerLabel } from "../src/components/CustomerPicker";
import { formatDistance, formatPrice, formatRating, shortId } from "../src/format";
import { categoryTrail, descriptionParts } from "../src/product/description";
import { formatParameter, formatSql } from "../src/recommend/sql";

describe("formats", () => {
  it("say prices in dollars, and nothing when there is none", () => {
    expect(formatPrice(1299)).toBe("US$ 1,299.00");
    expect(formatPrice(null)).toBeNull();
  });

  it("say distances like the api's sentences", () => {
    expect([formatDistance(3), formatDistance(846), formatDistance(1234)]).toEqual(["10 m", "850 m", "1.2 km"]);
  });

  it("say a rating with its votes", () => {
    expect(formatRating(4.66, 2100)).toBe("4.7 de 5 (2,100)");
    expect(formatRating(null, 3)).toBeNull();
  });

  it("shorten opaque ids and name customers by rank", () => {
    expect(shortId("AG3S4FROO422V5KP7DJCBXVUQLJQ")).toBe("AG3S4F…UQLJQ");
    expect(customerLabel("AG3S4FROO422V5KP7DJCBXVUQLJQ", 288, 0)).toBe("Cliente 1 · 288 compras · AG3S4F…UQLJQ");
    expect(customerLabel("USER-A", 0, -1)).toBe("Cliente USER-A");
  });
});

describe("product descriptions", () => {
  it("split the bullets from the paragraphs", () => {
    const parts = descriptionParts("Maple neck | Two humbuckers\n\nA solid body guitar.\n\nMade in Mexico.");
    expect(parts).toEqual({ bullets: ["Maple neck", "Two humbuckers"], paragraphs: ["A solid body guitar.", "Made in Mexico."] });
  });

  it("keep a description without bullets as paragraphs", () => {
    expect(descriptionParts("Just one paragraph.")).toEqual({ bullets: [], paragraphs: ["Just one paragraph."] });
    expect(descriptionParts(null)).toEqual({ bullets: [], paragraphs: [] });
  });

  it("drop the catalog's root from the category trail", () => {
    expect(categoryTrail("Musical Instruments > Guitars > Electric Guitars")).toEqual(["Guitars", "Electric Guitars"]);
    expect(categoryTrail("Instrument Accessories > Strings")).toEqual(["Instrument Accessories", "Strings"]);
  });
});

describe("signal sql", () => {
  it("break a query into one clause per line", () => {
    expect(formatSql("SELECT a FROM t WHERE b = $1 GROUP BY a ORDER BY a LIMIT $2")).toBe(
      "SELECT a\nFROM t\nWHERE b = $1\nGROUP BY a\nORDER BY a\nLIMIT $2",
    );
  });

  it("write parameters as they would go in the sql", () => {
    expect(["it's", 5, "vector(512)", { lat: -12.05, lon: -77.04 }, null].map(formatParameter)).toEqual([
      "'it''s'",
      "5",
      "vector(512)",
      "POINT(-12.05, -77.04)",
      "NULL",
    ]);
  });
});
