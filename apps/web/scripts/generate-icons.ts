/**
 * Genera los PNG del manifiesto desde los SVG de la carpeta dada.
 *
 *     node scripts/generate-icons.ts public/icons
 *
 * `icon.svg` tiene las esquinas redondeadas y fondo transparente (propósito `any`);
 * `icon-maskable.svg` llena todo el lienzo y deja el dibujo dentro de la zona segura del
 * 80 %, porque el sistema lo recorta con la forma que quiera.
 */
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Resvg } from "@resvg/resvg-js";

const RENDERS = [
  { source: "icon.svg", target: "icon-192.png", size: 192 },
  { source: "icon.svg", target: "icon-512.png", size: 512 },
  { source: "icon-maskable.svg", target: "icon-maskable-512.png", size: 512 },
  { source: "icon-maskable.svg", target: "apple-touch-icon-180.png", size: 180 },
] as const;

const [directory] = process.argv.slice(2);
if (directory === undefined) {
  console.error("uso: node scripts/generate-icons.ts <carpeta de iconos>");
  process.exit(2);
}
for (const { source, target, size } of RENDERS) {
  const svg = await readFile(join(directory, source));
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
  await writeFile(join(directory, target), png);
  console.log(join(directory, target));
}
