import { createHash } from "node:crypto";
import type { Plugin } from "vite";

import { themeBootScript } from "../theme/themePreference.ts";

/**
 * La fuente para `script-src` de una `Content-Security-Policy` que permita el guion de
 * arranque del tema y ningún otro guion en línea: `'sha256-…'` de su texto exacto.
 */
export function inlineScriptHash(script: string): string {
  return `'sha256-${createHash("sha256").update(script, "utf8").digest("base64")}'`;
}

/** Un guion sin `src`: su texto es el que el navegador compara con la CSP. */
const INLINE_SCRIPT = /<script\b(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)<\/script>/gi;

/**
 * Las fuentes `'sha256-…'` de cada guion en línea de un HTML ya construido, sin repetir y en
 * el orden en que aparecen. Es lo que el nginx de la app pone en `script-src`: el texto
 * exacto del guion de tema depende de cómo se cargó el kit al construir, así que se mide
 * sobre el `index.html` que se sirve y no sobre la fuente.
 */
export function inlineScriptHashes(html: string): string[] {
  const hashes = [...html.matchAll(INLINE_SCRIPT)].map((match) => inlineScriptHash(match[1] ?? ""));
  return [...new Set(hashes)];
}

export function themeBootScriptHash(): string {
  return inlineScriptHash(themeBootScript());
}

/**
 * Incrusta en el `<head>` el guion que aplica el tema antes de pintar, para que la página no
 * parpadee en el otro. Va en línea (no puede esperar a un archivo); su hash es
 * `themeBootScriptHash()`.
 */
export function themeBoot(): Plugin {
  return {
    name: "minigestor-theme-boot",
    transformIndexHtml: () => [{ tag: "script", children: themeBootScript(), injectTo: "head" }],
  };
}
