import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { DATASETS_DIR } from "./datasets.ts";

// La pista 2 del catálogo de muestra (`songcheck.sample`), que la carga indexó por tramos.
const SAMPLE_TRACK = join(DATASETS_DIR, "fma_small", "pistas", "muestra", "000002.wav");
const EXCERPT_START_SECONDS = 2.5;
const RIFF_HEADER_BYTES = 12;
const CHUNK_HEADER_BYTES = 8;

/** Un WAV PCM desde `startSeconds` hasta el final: la cabecera se rehace con el nuevo largo. */
function wavExcerpt(path: string, startSeconds: number): Buffer {
  const wav = readFileSync(path);
  let offset = RIFF_HEADER_BYTES;
  let format: Buffer | undefined;
  while (offset < wav.length) {
    const id = wav.toString("ascii", offset, offset + 4);
    const size = wav.readUInt32LE(offset + 4);
    const body = wav.subarray(offset + CHUNK_HEADER_BYTES, offset + CHUNK_HEADER_BYTES + size);
    if (id === "fmt ") {
      format = body;
    } else if (id === "data" && format !== undefined) {
      const bytesPerSecond = format.readUInt32LE(8);
      const blockAlign = format.readUInt16LE(12);
      const skipped = Math.round((startSeconds * bytesPerSecond) / blockAlign) * blockAlign;
      const samples = body.subarray(skipped);
      const header = Buffer.alloc(RIFF_HEADER_BYTES + CHUNK_HEADER_BYTES + format.length + CHUNK_HEADER_BYTES);
      header.write("RIFF", 0, "ascii");
      header.writeUInt32LE(header.length - CHUNK_HEADER_BYTES + samples.length, 4);
      header.write("WAVEfmt ", 8, "ascii");
      header.writeUInt32LE(format.length, 16);
      format.copy(header, 20);
      header.write("data", 20 + format.length, "ascii");
      header.writeUInt32LE(samples.length, 24 + format.length);
      return Buffer.concat([header, samples]);
    }
    offset += CHUNK_HEADER_BYTES + size + (size % 2);
  }
  throw new Error(`${path} no es un WAV PCM con sus bloques fmt y data`);
}

test("an excerpt of a catalog track is flagged with its original, its offset and one-click listening", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Audio para analizar").setInputFiles({
    name: "extracto.wav",
    mimeType: "audio/wav",
    buffer: wavExcerpt(SAMPLE_TRACK, EXCERPT_START_SECONDS),
  });

  await expect(page.getByRole("heading", { name: /^Posible uso no autorizado: 1 fragmento de 1 canción/ })).toBeVisible();
  const spans = page.getByRole("group", { name: /Tramos que coinciden/ }).getByRole("button");
  await expect(spans.first()).toHaveAccessibleName(/^A: Melodía 2 de Muestra\..*desfase −0:02,5\.$/);
  await expect(page.getByText("Original: Melodía 2")).toBeVisible();
  await expect(page.getByText(/desfase −0:02,5/).first()).toBeVisible();

  const original = page.waitForResponse((response) => /\/api\/songs\/2\/audio$/.test(response.url()) && response.ok());
  await page.getByRole("button", { name: /^Escuchar el original Melodía 2/ }).first().click();
  await original;
  await expect(page.getByRole("button", { name: /^Pausar el original Melodía 2/ }).first()).toBeVisible();
  await spans.first().click();
  await expect(spans.first()).toHaveAttribute("data-playing", "true");
});
