/** La opción activa tras una flecha: baja o sube y da la vuelta; `-1` es el campo de texto. */
export function nextActiveIndex(current: number, count: number, step: 1 | -1): number {
  if (count === 0) {
    return -1;
  }
  const next = current + step;
  if (next < -1) {
    return count - 1;
  }
  return next >= count ? -1 : next;
}
