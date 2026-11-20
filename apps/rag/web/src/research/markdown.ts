import { segmentAnswer } from "./citations";

/**
 * El Markdown que escribe el modelo, reducido a lo que la respuesta muestra: párrafos,
 * listas, citas en bloque, código y, dentro del texto, negritas, cursivas, código y las citas
 * `[n]`. Se convierte en datos y la vista los pinta como elementos de React: ningún HTML del
 * modelo llega al DOM. Funciona con texto a medio llegar (un bloque de código sin cerrar es
 * código hasta el final).
 */
export type AnswerBlock =
  | { readonly kind: "paragraph"; readonly text: string }
  | { readonly kind: "heading"; readonly text: string }
  | { readonly kind: "quote"; readonly text: string }
  | { readonly kind: "code"; readonly text: string }
  | { readonly kind: "list"; readonly ordered: boolean; readonly items: readonly string[] };

export type InlineNode =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "code"; readonly text: string }
  | { readonly kind: "strong"; readonly children: readonly InlineNode[] }
  | { readonly kind: "em"; readonly children: readonly InlineNode[] }
  | { readonly kind: "citation"; readonly numbers: readonly number[] };

const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const BULLET = /^\s{0,3}[-*•]\s+(.*)$/;
const NUMBERED = /^\s{0,3}\d+[.)]\s+(.*)$/;
const INDENTED = /^\s{2,}\S/;
const BLANK = /^\s*$/;
const EMPHASIS = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*|__[^_\n]+?__)|(\*[^*\s][^*\n]*?\*)/g;

interface ListItemLine {
  readonly ordered: boolean;
  readonly text: string;
}

function listItem(line: string): ListItemLine | null {
  const bullet = BULLET.exec(line);
  if (bullet !== null) {
    return { ordered: false, text: bullet[1] ?? "" };
  }
  const numbered = NUMBERED.exec(line);
  return numbered === null ? null : { ordered: true, text: numbered[1] ?? "" };
}

class BlockBuilder {
  readonly blocks: AnswerBlock[] = [];
  private paragraph: string[] = [];
  private list: { ordered: boolean; items: string[] } | null = null;
  private quote: string[] = [];

  addParagraphLine(line: string): void {
    this.closeList();
    this.closeQuote();
    this.paragraph.push(line.trim());
  }

  addListItem(item: ListItemLine): void {
    this.closeParagraph();
    this.closeQuote();
    if (this.list !== null && this.list.ordered !== item.ordered) {
      this.closeList();
    }
    this.list ??= { ordered: item.ordered, items: [] };
    this.list.items.push(item.text.trim());
  }

  continueListItem(line: string): boolean {
    const items = this.list?.items;
    if (items === undefined || items.length === 0 || this.paragraph.length > 0) {
      return false;
    }
    items[items.length - 1] = `${items[items.length - 1]} ${line.trim()}`;
    return true;
  }

  addQuoteLine(text: string): void {
    this.closeParagraph();
    this.closeList();
    this.quote.push(text.trim());
  }

  add(block: AnswerBlock): void {
    this.closeAll();
    this.blocks.push(block);
  }

  /** Una línea en blanco cierra el párrafo; la lista sigue abierta por si continúa. */
  blank(): void {
    this.closeParagraph();
    this.closeQuote();
  }

  closeAll(): void {
    this.closeParagraph();
    this.closeList();
    this.closeQuote();
  }

  private closeParagraph(): void {
    if (this.paragraph.length > 0) {
      this.blocks.push({ kind: "paragraph", text: this.paragraph.join(" ") });
      this.paragraph = [];
    }
  }

  private closeList(): void {
    if (this.list !== null) {
      this.blocks.push({ kind: "list", ordered: this.list.ordered, items: this.list.items });
      this.list = null;
    }
  }

  private closeQuote(): void {
    if (this.quote.length > 0) {
      this.blocks.push({ kind: "quote", text: this.quote.join(" ") });
      this.quote = [];
    }
  }
}

function fencedCode(lines: readonly string[], start: number): { readonly text: string; readonly next: number } {
  let end = start + 1;
  while (end < lines.length && !FENCE.test(lines[end] ?? "")) {
    end += 1;
  }
  return { text: lines.slice(start + 1, end).join("\n"), next: end + 1 };
}

export function answerBlocks(text: string): AnswerBlock[] {
  const lines = text.split("\n");
  const builder = new BlockBuilder();
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (FENCE.test(line)) {
      const code = fencedCode(lines, index);
      builder.add({ kind: "code", text: code.text });
      index = code.next;
      continue;
    }
    index += 1;
    if (BLANK.test(line)) {
      builder.blank();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      builder.add({ kind: "heading", text: (heading[1] ?? "").trim() });
      continue;
    }
    const quote = QUOTE.exec(line);
    if (quote !== null) {
      builder.addQuoteLine(quote[1] ?? "");
      continue;
    }
    const item = listItem(line);
    if (item !== null) {
      builder.addListItem(item);
      continue;
    }
    if (INDENTED.test(line) && builder.continueListItem(line)) {
      continue;
    }
    builder.addParagraphLine(line);
  }
  builder.closeAll();
  return builder.blocks;
}

function withCitations(text: string): InlineNode[] {
  return segmentAnswer(text).map((segment) =>
    segment.kind === "text" ? { kind: "text", text: segment.text } : { kind: "citation", numbers: segment.numbers },
  );
}

function emphasized(token: string): InlineNode {
  if (token.startsWith("`")) {
    return { kind: "code", text: token.slice(1, -1) };
  }
  if (token.startsWith("**") || token.startsWith("__")) {
    return { kind: "strong", children: withCitations(token.slice(2, -2)) };
  }
  return { kind: "em", children: withCitations(token.slice(1, -1)) };
}

/** El texto de un bloque con su código, sus énfasis y sus citas. */
export function inlineNodes(text: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(EMPHASIS)) {
    if (match.index > cursor) {
      nodes.push(...withCitations(text.slice(cursor, match.index)));
    }
    nodes.push(emphasized(match[0]));
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    nodes.push(...withCitations(text.slice(cursor)));
  }
  return nodes;
}
