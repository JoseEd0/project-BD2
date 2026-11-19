const SECONDS_PER_MINUTE = 60;
const CLOCK_SECONDS = new Intl.NumberFormat("es", { minimumIntegerDigits: 2, maximumFractionDigits: 1 });
const PERCENT = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 0 });
const MILLISECONDS = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
const MINUS_SIGN = "−";

/** `75.5` → `1:15,5`; las décimas solo si las hay. */
export function formatClock(seconds: number): string {
  const whole = Math.max(0, seconds);
  const minutes = Math.floor(whole / SECONDS_PER_MINUTE);
  const rest = Math.round((whole - minutes * SECONDS_PER_MINUTE) * 10) / 10;
  return `${minutes}:${CLOCK_SECONDS.format(rest)}`;
}

/** El desfase con su signo siempre visible: `+1:00`, `−0:02,5`, `0:00`. */
export function formatOffset(seconds: number): string {
  if (seconds === 0) {
    return formatClock(0);
  }
  return `${seconds > 0 ? "+" : MINUS_SIGN}${formatClock(Math.abs(seconds))}`;
}

export function formatSpan(start: number, end: number): string {
  return `${formatClock(start)}–${formatClock(end)}`;
}

export function formatPercent(share: number): string {
  return PERCENT.format(share);
}

export function formatMilliseconds(milliseconds: number): string {
  return `${MILLISECONDS.format(milliseconds)} ms`;
}

const BYTES_PER_KIB = 1024;
const SIZE = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });

export function formatBytes(bytes: number): string {
  const kib = bytes / BYTES_PER_KIB;
  return kib < BYTES_PER_KIB ? `${SIZE.format(kib)} KB` : `${SIZE.format(kib / BYTES_PER_KIB)} MB`;
}
