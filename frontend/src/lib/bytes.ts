const UNITS = ["B", "KB", "MB", "GB"];
const STEP = 1024;
const DECIMALS = 1;

/** Un tamaño en bytes con la unidad que lo deja entre 1 y 1 024: «48,0 KB». */
export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= STEP && unit < UNITS.length - 1) {
    value /= STEP;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : DECIMALS;
  return `${value.toLocaleString("es", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${UNITS[unit]}`;
}
