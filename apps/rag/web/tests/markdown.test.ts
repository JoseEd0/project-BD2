import { describe, expect, it } from "vitest";

import { answerBlocks, inlineNodes } from "../src/research/markdown";

describe("answerBlocks", () => {
  it("splits paragraphs and recognizes bullet and numbered lists", () => {
    expect(answerBlocks("Primero\nsigue.\n\n- uno [1]\n- dos\n\n1. a\n2) b\n\n")).toEqual([
      { kind: "paragraph", text: "Primero sigue." },
      { kind: "list", ordered: false, items: ["uno [1]", "dos"] },
      { kind: "list", ordered: true, items: ["a", "b"] },
    ]);
  });

  it("ends a paragraph where a list starts and keeps indented continuation lines in the item", () => {
    expect(answerBlocks("Tres pasos:\n1. leer\n   el nodo\n2. comparar")).toEqual([
      { kind: "paragraph", text: "Tres pasos:" },
      { kind: "list", ordered: true, items: ["leer el nodo", "comparar"] },
    ]);
  });

  it("keeps a loose list together across blank lines", () => {
    expect(answerBlocks("- a\n\n- b")).toEqual([{ kind: "list", ordered: false, items: ["a", "b"] }]);
  });

  it("reads fenced code verbatim, also while it is still unclosed", () => {
    expect(answerBlocks("Así:\n```sql\nSELECT *\n  FROM t\n```\nListo.")).toEqual([
      { kind: "paragraph", text: "Así:" },
      { kind: "code", text: "SELECT *\n  FROM t" },
      { kind: "paragraph", text: "Listo." },
    ]);
    expect(answerBlocks("```\nSELECT")).toEqual([{ kind: "code", text: "SELECT" }]);
  });

  it("turns headings and quotes into their own blocks", () => {
    expect(answerBlocks("## Idea\n> cita\n> sigue")).toEqual([
      { kind: "heading", text: "Idea" },
      { kind: "quote", text: "cita sigue" },
    ]);
  });
});

describe("inlineNodes", () => {
  it("parses bold, italics, code and citations, also inside bold", () => {
    expect(inlineNodes("Un **B+ tree [1]** usa `fanout` y *hojas* [2, 3].")).toEqual([
      { kind: "text", text: "Un " },
      { kind: "strong", children: [{ kind: "text", text: "B+ tree " }, { kind: "citation", numbers: [1] }] },
      { kind: "text", text: " usa " },
      { kind: "code", text: "fanout" },
      { kind: "text", text: " y " },
      { kind: "em", children: [{ kind: "text", text: "hojas" }] },
      { kind: "text", text: " " },
      { kind: "citation", numbers: [2, 3] },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves markup as text: nothing becomes HTML", () => {
    expect(inlineNodes("<img src=x onerror=alert(1)> **sin cerrar")).toEqual([
      { kind: "text", text: "<img src=x onerror=alert(1)> **sin cerrar" },
    ]);
  });
});
