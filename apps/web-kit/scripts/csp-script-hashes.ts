/**
 * Escribe en la salida estándar las fuentes `'sha256-…'` de los guiones en línea de un
 * `index.html` ya construido, separadas por espacios. La imagen de cada interfaz lo corre
 * sobre su build para armar la `Content-Security-Policy` de su nginx:
 *
 *     node web-kit/scripts/csp-script-hashes.ts dist/index.html
 */
import { readFile } from "node:fs/promises";

import { inlineScriptHashes } from "../src/vite/themeBoot.ts";

const [indexHtml] = process.argv.slice(2);
if (indexHtml === undefined) {
  console.error("uso: node scripts/csp-script-hashes.ts <index.html construido>");
  process.exit(2);
}
const hashes = inlineScriptHashes(await readFile(indexHtml, "utf8"));
if (hashes.length === 0) {
  console.error(`${indexHtml} no tiene guiones en línea: falta el del tema`);
  process.exit(1);
}
console.log(hashes.join(" "));
