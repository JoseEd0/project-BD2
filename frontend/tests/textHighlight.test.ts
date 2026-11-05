import assert from "node:assert/strict";
import { test } from "node:test";

import {
  excerpt,
  foldWord,
  highlightSegments,
  withoutLeadingTitle,
} from "../src/lib/textHighlight.ts";

function marked(text: string, terms: string[]): string[] {
  return highlightSegments(text, terms)
    .filter((segment) => segment.match)
    .map((segment) => segment.text);
}

test("una palabra se pliega como en el motor: sin tildes ni apóstrofos, con la ñ", () => {
  assert.equal(foldWord("Inalámbricos"), "inalambricos");
  assert.equal(foldWord("PINGÜINO"), "pinguino");
  assert.equal(foldWord("Año"), "año");
  assert.equal(foldWord("L'Oréal"), "loreal");
  assert.equal(foldWord("ﬁltro"), "filtro");
});

test("se marcan las palabras que empiezan por una raíz, sin importar tildes ni mayúsculas", () => {
  const text = "Auriculares Bluetooth, Auricular inalámbrico con estuche";
  assert.deepEqual(marked(text, ["auricular", "inalambr"]), [
    "Auriculares",
    "Auricular",
    "inalámbrico",
  ]);
});

test("concatenar los trozos devuelve el texto original", () => {
  const text = "Mochila «antirrobo» impermeable, 15,6''; USB-C y más…";
  const joined = highlightSegments(text, ["mochil", "imperm", "c"])
    .map((segment) => segment.text)
    .join("");
  assert.equal(joined, text);
});

test("sin términos, el texto queda en un solo trozo sin marcar", () => {
  assert.deepEqual(highlightSegments("Funda iPhone", []), [{ text: "Funda iPhone", match: false }]);
  assert.deepEqual(highlightSegments("", ["fund"]), []);
});

test("una raíz corta solo marca la palabra idéntica", () => {
  assert.deepEqual(marked("Cable USB-C a C, cargador", ["c"]), ["C", "C"]);
});

test("una cifra se compara entera y con la coma decimal como punto", () => {
  assert.deepEqual(marked("Portátil 15,6 pulgadas, 156 Hz, 15.6 mm", ["15.6"]), ["15,6", "15.6"]);
  assert.deepEqual(marked("iPhone12 y iPhone 128", ["12"]), ["12"]);
});

test("las letras y las cifras pegadas se separan como en el analizador", () => {
  assert.deepEqual(marked("Disco 128GB", ["gb", "128"]), ["128", "GB"]);
});

test("el fragmento empieza poco antes de la primera coincidencia y no parte palabras", () => {
  const filler = "palabra ".repeat(40);
  const text = `${filler}aquí viene la cafetera italiana de inducción ${filler}`;
  const fragment = excerpt(text, ["cafeter"], 80, 20);
  assert.ok(fragment.startsWith("…"));
  assert.ok(fragment.endsWith("…"));
  assert.ok(fragment.includes("cafetera"));
  assert.ok(fragment.length <= 80 + 30);
  const words = new Set(text.split(/\s+/));
  for (const word of fragment.replace(/…/g, "").split(/\s+/)) {
    assert.ok(words.has(word), `«${word}» es un trozo de palabra`);
  }
});

test("un texto corto se devuelve entero", () => {
  assert.equal(excerpt("Funda iPhone", ["fund"], 80, 20), "Funda iPhone");
});

test("sin coincidencias, el fragmento es el principio del texto", () => {
  const text = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce";
  assert.equal(excerpt(text, ["zzz"], 20, 5), "uno dos tres cuatro cinco…");
});

test("el título repetido al principio del texto se quita", () => {
  assert.equal(withoutLeadingTitle("Funda iPhone Funda de piel", "Funda iPhone"), "Funda de piel");
  assert.equal(withoutLeadingTitle("Otra cosa", "Funda iPhone"), "Otra cosa");
  assert.equal(withoutLeadingTitle("Texto", null), "Texto");
});

test("de una consulta booleana solo se marcan los términos positivos que trae el API", () => {
  // 'funda AND iphone AND NOT silicona' llega como fund, iphon: ni silicona ni los operadores.
  const text = "Funda de silicona para iPhone and more";
  assert.deepEqual(marked(text, ["fund", "iphon"]), ["Funda", "iPhone"]);
});
