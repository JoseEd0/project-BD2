import { createHash } from "node:crypto";
import type { HtmlTagDescriptor } from "vite";
import { describe, expect, it } from "vitest";

import { themeBootScript } from "../src/theme/themePreference.ts";
import { inlineScriptHash, inlineScriptHashes, themeBoot, themeBootScriptHash } from "../src/vite/index.ts";

function injectedTags(): HtmlTagDescriptor[] {
  const hook = themeBoot().transformIndexHtml;
  if (typeof hook !== "function") {
    throw new Error("themeBoot debe transformar index.html con una función");
  }
  return hook.call({} as never, "", {} as never) as HtmlTagDescriptor[];
}

describe("themeBoot", () => {
  it("injects the boot script in the head, before the body paints", () => {
    expect(injectedTags()).toEqual([{ tag: "script", children: themeBootScript(), injectTo: "head" }]);
  });

  it("publishes the CSP hash of exactly the script it injects", () => {
    const [tag] = injectedTags();
    const digest = createHash("sha256").update(String(tag?.children)).digest("base64");
    expect(themeBootScriptHash()).toBe(`'sha256-${digest}'`);
  });

  it("hashes the UTF-8 bytes, as browsers do", () => {
    // El ejemplo de la especificación de CSP: `alert('Hello, world.');`.
    expect(inlineScriptHash("alert('Hello, world.');")).toBe("'sha256-qznLcsROx4GACP2dm0UCKCzCG+HiZ1guq6ZZDob/Tng='");
  });
});

describe("inlineScriptHashes", () => {
  const built = [
    "<!doctype html><html><head>",
    `<script>${themeBootScript()}</script>`,
    '<script type="module" crossorigin src="/assets/index-abc.js"></script>',
    "<script id=\"otro\">\n  console.log(1);\n</script>",
    `<script>${themeBootScript()}</script>`,
    "</head><body></body></html>",
  ].join("\n");

  it("hashes every inline script of a built page, once each, in order", () => {
    expect(inlineScriptHashes(built)).toEqual([themeBootScriptHash(), inlineScriptHash("\n  console.log(1);\n")]);
  });

  it("leaves out scripts loaded from a file", () => {
    expect(inlineScriptHashes('<script type="module" src="/a.js"></script>')).toEqual([]);
  });
});
