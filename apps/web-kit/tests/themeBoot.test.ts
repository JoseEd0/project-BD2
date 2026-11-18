import { createHash } from "node:crypto";
import type { HtmlTagDescriptor } from "vite";
import { describe, expect, it } from "vitest";

import { themeBootScript } from "../src/theme/themePreference.ts";
import { inlineScriptHash, themeBoot, themeBootScriptHash } from "../src/vite/index.ts";

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
