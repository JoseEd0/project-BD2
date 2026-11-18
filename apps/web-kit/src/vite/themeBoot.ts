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
