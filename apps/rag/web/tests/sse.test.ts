import { describe, expect, it } from "vitest";

import { SseParser, type ServerSentEvent } from "../src/research/sse";

const STREAM =
  'event: sources\ndata: {"n":1}\n\n' +
  ": un comentario\n" +
  'event: delta\ndata: {"text":"índice ñ 🙂"}\n\n' +
  "data: primera\ndata: segunda\n\n" +
  'event: done\ndata: {"ok":true}\n\n';

const EXPECTED: ServerSentEvent[] = [
  { event: "sources", data: '{"n":1}', id: null },
  { event: "delta", data: '{"text":"índice ñ 🙂"}', id: null },
  { event: "message", data: "primera\nsegunda", id: null },
  { event: "done", data: '{"ok":true}', id: null },
];

function parseInPieces(text: string, cuts: readonly number[]): ServerSentEvent[] {
  const parser = new SseParser();
  const events: ServerSentEvent[] = [];
  let start = 0;
  for (const cut of [...cuts, text.length]) {
    events.push(...parser.push(text.slice(start, cut)));
    start = cut;
  }
  return events;
}

describe("SseParser", () => {
  it("reads named events, default events, multi-line data and skips comments", () => {
    expect(new SseParser().push(STREAM)).toEqual(EXPECTED);
  });

  it("gives the same events wherever the body is cut", () => {
    for (let cut = 1; cut < STREAM.length; cut += 1) {
      expect(parseInPieces(STREAM, [cut])).toEqual(EXPECTED);
    }
  });

  it("gives the same events when every character arrives alone", () => {
    const cuts = Array.from({ length: STREAM.length - 1 }, (_, index) => index + 1);
    expect(parseInPieces(STREAM, cuts)).toEqual(EXPECTED);
  });

  it("accepts CRLF and lone CR line breaks, even with the CRLF split across chunks", () => {
    const crlf = STREAM.replaceAll("\n", "\r\n");
    for (let cut = 1; cut < crlf.length; cut += 1) {
      expect(parseInPieces(crlf, [cut])).toEqual(EXPECTED);
    }
    const crOnly = new SseParser();
    expect([...crOnly.push(STREAM.replaceAll("\n", "\r")), ...crOnly.finish()]).toEqual(EXPECTED);
  });

  it("drops a leading byte order mark and an unterminated last event", () => {
    const parser = new SseParser();
    expect(parser.push('﻿event: delta\ndata: "a"\n\nevent: done\ndata: "cut"')).toEqual([{ event: "delta", data: '"a"', id: null }]);
    expect(parser.finish()).toEqual([]);
    expect(parser.push("\n\n")).toEqual([]);
  });

  it("keeps the space after the colon only once and reads fields without a value", () => {
    expect(new SseParser().push("data:sin espacio\n\ndata:  dos\n\ndata\n\n")).toEqual([
      { event: "message", data: "sin espacio", id: null },
      { event: "message", data: " dos", id: null },
      { event: "message", data: "", id: null },
    ]);
  });
});
