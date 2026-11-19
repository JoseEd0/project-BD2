/**
 * Un PDF mínimo escrito a mano: una página por cada lista de líneas, en Helvetica (una de las
 * 14 fuentes estándar, que no se incrusta). Basta para que pdf.js dibuje la página y extraiga
 * su capa de texto, sin traer un generador de PDF al proyecto.
 */
const PAGE_WIDTH = 612;
const PAGE_HEIGHT = 792;
const MARGIN = 72;
const FONT_SIZE = 14;
const LEADING = 22;

function escapeText(text: string): string {
  return text.replace(/[\\()]/g, (character) => `\\${character}`);
}

function contentStream(lines: readonly string[]): string {
  const shown = lines.map((line, index) => `1 0 0 1 ${MARGIN} ${PAGE_HEIGHT - MARGIN - index * LEADING} Tm (${escapeText(line)}) Tj`).join("\n");
  return `BT\n/F1 ${FONT_SIZE} Tf\n${shown}\nET`;
}

export function pdfBytes(pages: readonly (readonly string[])[]): Buffer {
  const objects: string[] = [];
  const pageIds = pages.map((_, index) => 4 + index * 2);
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pages.length} >>`;
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>";
  pages.forEach((lines, index) => {
    const pageId = pageIds[index] ?? 0;
    const stream = contentStream(lines);
    objects[pageId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Resources << /Font << /F1 3 0 R >> >> /Contents ${pageId + 1} 0 R >>`;
    objects[pageId + 1] = `<< /Length ${Buffer.byteLength(stream, "latin1")} >>\nstream\n${stream}\nendstream`;
  });
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  for (let id = 1; id < objects.length; id += 1) {
    offsets[id] = Buffer.byteLength(body, "latin1");
    body += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = Buffer.byteLength(body, "latin1");
  const entries = offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  body += `xref\n0 ${objects.length}\n0000000000 65535 f \n${entries}trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}
