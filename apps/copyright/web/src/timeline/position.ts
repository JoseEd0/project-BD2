/** Una fracción entre 0 y 1 como porcentaje CSS. */
export function percent(share: number): string {
  return `${(Math.min(1, Math.max(0, share)) * 100).toFixed(3)}%`;
}
