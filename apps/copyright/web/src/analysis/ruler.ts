/** Pasos de la regla en segundos, de menor a mayor: siempre números redondos de reloj. */
const RULER_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300] as const;

/** Las marcas de la regla de una duración: el paso más fino que no pasa de `maxTicks`. */
export function rulerTicks(durationSeconds: number, maxTicks: number): number[] {
  if (durationSeconds <= 0 || maxTicks < 1) {
    return [0];
  }
  const step = RULER_STEPS.find((candidate) => durationSeconds / candidate <= maxTicks) ?? RULER_STEPS[RULER_STEPS.length - 1]!;
  const ticks: number[] = [];
  for (let tick = 0; tick <= durationSeconds + 1e-9; tick += step) {
    ticks.push(tick);
  }
  return ticks;
}

/**
 * Las barras de compás: una por salto de la detección (cada ventana empieza en una), salvo
 * que sean tantas que se vuelvan una mancha; entonces una de cada tantas.
 */
export function barlineTimes(durationSeconds: number, hopSeconds: number, maxLines: number): number[] {
  if (durationSeconds <= 0 || hopSeconds <= 0 || maxLines < 1) {
    return [];
  }
  const every = Math.max(1, Math.ceil(durationSeconds / hopSeconds / maxLines));
  const step = hopSeconds * every;
  const lines: number[] = [];
  for (let time = step; time < durationSeconds - 1e-9; time += step) {
    lines.push(time);
  }
  return lines;
}
