/**
 * Resaltado de los términos de una búsqueda de texto sobre el texto que se muestra.
 *
 * El API devuelve los términos ya analizados —plegados, sin stopwords y reducidos a su raíz:
 * «auriculares inalámbricos» llega como `auricular`, `inalambr`—, pero en pantalla está el
 * texto original. De una consulta booleana solo llegan los términos positivos, así que ni
 * los negados ni los operadores se marcan. El resaltado es una aproximación del lado del
 * cliente:
 *
 * 1. se corta el texto en palabras y cifras con la misma regla que el analizador del motor
 *    (`src/text/analyzer.py`): una cifra puede llevar separadores decimales o de miles y
 *    todo lo que no es letra ni cifra separa;
 * 2. cada palabra se pliega como `fold` (`src/text/normalization.py`): NFKC, minúsculas, sin
 *    tildes ni diéresis salvo la ñ y sin apóstrofos;
 * 3. una palabra se marca si su forma plegada **empieza por** una de las raíces, y una cifra
 *    si es igual a un término numérico.
 *
 * No se repite el stemmer de Snowball en el navegador, así que hay dos desvíos conocidos:
 * una raíz que el stemmer reescribe en vez de recortar («potencia» → `potent`) no marca la
 * palabra, y una raíz marca también palabras más largas que la contienen al principio
 * (`mochil` marca «mochilero»). Una raíz de menos de tres letras solo marca la palabra
 * idéntica, para que `c` (de «usb-c») no ilumine media ficha.
 */

/** Un trozo del texto y si es una coincidencia con la consulta. */
export interface Segment {
  text: string;
  match: boolean;
}

const WORD_PATTERN = /(\p{Nd}+(?:[.,]\p{Nd}+)*)|(\p{L}[\p{L}\p{M}'’ʼ]*)/gu;
const NUMBER_GROUP = 1;
const APOSTROPHES = /['’ʼ]/gu;
const COMBINING_MARKS = /\p{Mn}/gu;
const PRESERVED_LETTER = "ñ";
const DECIMAL_COMMA = /,/g;
const DECIMAL_POINT = ".";
const MIN_PREFIX_LENGTH = 3;
const ELLIPSIS = "…";
const WHITESPACE = /\s/u;

/** Una palabra plegada como la pliega el motor antes de buscarla en el índice. */
export function foldWord(word: string): string {
  const lowered = word.normalize("NFKC").toLowerCase().replace(APOSTROPHES, "");
  return Array.from(lowered, (character) =>
    character === PRESERVED_LETTER
      ? character
      : character.normalize("NFKD").replace(COMBINING_MARKS, ""),
  ).join("");
}

function isNumberTerm(term: string): boolean {
  return /^\p{Nd}/u.test(term);
}

/** Decide si una palabra o cifra del texto corresponde a algún término de la consulta. */
export function matcherFor(
  terms: readonly string[],
): (token: string, isNumber: boolean) => boolean {
  const numbers = new Set(terms.filter(isNumberTerm));
  const stems = terms.filter((term) => !isNumberTerm(term));
  return (token, isNumber) => {
    if (isNumber) {
      return numbers.has(token.replace(DECIMAL_COMMA, DECIMAL_POINT));
    }
    const folded = foldWord(token);
    return stems.some((stem) =>
      stem.length < MIN_PREFIX_LENGTH ? folded === stem : folded.startsWith(stem),
    );
  };
}

/** El texto partido en trozos, con las palabras de la consulta marcadas. Concatenar los
 * trozos devuelve el texto original sin cambios. */
export function highlightSegments(text: string, terms: readonly string[]): Segment[] {
  if (terms.length === 0 || text.length === 0) {
    return text.length === 0 ? [] : [{ text, match: false }];
  }
  const matches = matcherFor(terms);
  const segments: Segment[] = [];
  let cursor = 0;
  for (const found of text.matchAll(WORD_PATTERN)) {
    const isNumber = found[NUMBER_GROUP] !== undefined;
    if (!matches(found[0], isNumber)) {
      continue;
    }
    const start = found.index ?? 0;
    if (start > cursor) {
      segments.push({ text: text.slice(cursor, start), match: false });
    }
    segments.push({ text: found[0], match: true });
    cursor = start + found[0].length;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), match: false });
  }
  return segments;
}

/** Posición de la primera coincidencia del texto, o `null` si no tiene ninguna. */
function firstMatch(text: string, terms: readonly string[]): number | null {
  const matches = matcherFor(terms);
  for (const found of text.matchAll(WORD_PATTERN)) {
    if (matches(found[0], found[NUMBER_GROUP] !== undefined)) {
      return found.index ?? 0;
    }
  }
  return null;
}

function wordStartAtOrBefore(text: string, position: number): number {
  let start = Math.max(0, position);
  while (start > 0 && !WHITESPACE.test(text[start - 1])) {
    start -= 1;
  }
  return start;
}

function wordEndAtOrAfter(text: string, position: number): number {
  let end = Math.min(text.length, position);
  while (end < text.length && !WHITESPACE.test(text[end])) {
    end += 1;
  }
  return end;
}

/**
 * Un fragmento de como mucho `maxLength` caracteres (más los puntos suspensivos) alrededor
 * de la primera coincidencia, con `leading` caracteres de contexto antes de ella. Corta en
 * espacios, así que no parte palabras; sin coincidencias, el principio del texto.
 */
export function excerpt(
  text: string,
  terms: readonly string[],
  maxLength: number,
  leading: number,
): string {
  if (text.length <= maxLength) {
    return text;
  }
  const anchor = firstMatch(text, terms) ?? 0;
  const start = wordStartAtOrBefore(text, Math.min(anchor - leading, text.length - maxLength));
  const end = wordEndAtOrAfter(text, start + maxLength);
  const prefix = start > 0 ? ELLIPSIS : "";
  const suffix = end < text.length ? ELLIPSIS : "";
  return `${prefix}${text.slice(start, end).trim()}${suffix}`;
}

/** El texto sin el título al principio, si empieza por él: las fichas de producto repiten
 * el título como primera frase, y en un resultado ya se ve arriba. */
export function withoutLeadingTitle(text: string, title: string | null): string {
  if (title === null || title.length === 0 || !text.startsWith(title)) {
    return text;
  }
  return text.slice(title.length).trimStart();
}
