/** Cómo se escriben las cifras en la interfaz: en español, con unidad y sin falsa precisión. */
const MILLISECONDS = new Intl.NumberFormat("es", { maximumFractionDigits: 0 });
const SECONDS = new Intl.NumberFormat("es", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const INTEGER = new Intl.NumberFormat("es");
const SCORE = new Intl.NumberFormat("es", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const USD = new Intl.NumberFormat("es", { style: "currency", currency: "USD", minimumFractionDigits: 4, maximumFractionDigits: 4 });
const SHARE = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 0 });

const SECOND_MS = 1000;

export function formatDuration(milliseconds: number): string {
  return milliseconds < SECOND_MS ? `${MILLISECONDS.format(milliseconds)} ms` : `${SECONDS.format(milliseconds / SECOND_MS)} s`;
}

export function formatCount(value: number): string {
  return INTEGER.format(value);
}

export function formatScore(value: number): string {
  return SCORE.format(value);
}

export function formatCost(value: number | null): string {
  return value === null ? "sin precio" : USD.format(value);
}

export function formatShare(value: number): string {
  return SHARE.format(value);
}

export function formatPages(start: number, end: number): string {
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`;
}

/** «Tsitsigkos, Michalopoulos y otros 2», o el único autor; vacío si no hay. */
export function formatAuthors(authors: readonly string[]): string {
  const surnames = authors.map((author) => author.trim().split(/\s+/).at(-1) ?? author);
  if (surnames.length <= 2) {
    return surnames.join(" y ");
  }
  return `${surnames.slice(0, 2).join(", ")} y otros ${surnames.length - 2}`;
}
