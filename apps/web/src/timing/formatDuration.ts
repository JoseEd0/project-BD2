const FINE_DURATION_LIMIT_MS = 10;
const COARSE_DURATION_LIMIT_MS = 100;
const FINE_FRACTION_DIGITS = 2;
const MEDIUM_FRACTION_DIGITS = 1;
const COARSE_FRACTION_DIGITS = 0;
const LOCALE = "es";

function fixedDigits(digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

const fine = fixedDigits(FINE_FRACTION_DIGITS);
const medium = fixedDigits(MEDIUM_FRACTION_DIGITS);
const coarse = fixedDigits(COARSE_FRACTION_DIGITS);
const percent = new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits: COARSE_FRACTION_DIGITS });

/** Milisegundos con la precisión que importa a esa escala: 0,41 · 12,3 · 340. */
export function formatMilliseconds(milliseconds: number): string {
  if (milliseconds < FINE_DURATION_LIMIT_MS) {
    return fine.format(milliseconds);
  }
  return (milliseconds < COARSE_DURATION_LIMIT_MS ? medium : coarse).format(milliseconds);
}

export function formatShare(share: number): string {
  return percent.format(share);
}
