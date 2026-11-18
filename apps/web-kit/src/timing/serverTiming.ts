/**
 * Lectura de la cabecera `Server-Timing` (W3C Server Timing) y su reparto por etapa.
 *
 * El gestor y el API de cada app la emiten como
 * `parse;dur=0.412, plan;dur=0.130, exec;dur=3.201, total;dur=4.020`.
 * Una cabecera ausente, vacía o con entradas mal formadas no rompe nada: lo que no se
 * entiende se descarta y el resto se muestra.
 */

export const SERVER_TIMING_HEADER = "Server-Timing";

/** Nombre de la métrica que mide la petición completa en toda la plataforma. */
const TOTAL_METRIC = "total";

export interface TimingMetric {
  readonly name: string;
  readonly durationMs: number | null;
  readonly description: string | null;
}

export interface StageSlice {
  readonly name: string;
  readonly description: string | null;
  readonly durationMs: number;
  /** Parte de la barra, entre 0 y 1. */
  readonly share: number;
}

export interface StageBreakdown {
  readonly stages: readonly StageSlice[];
  readonly totalMs: number;
  /** Lo que el total mide y ninguna etapa explica: middleware, red interna, colas. */
  readonly unattributedMs: number;
}

const TOKEN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const QUOTE = '"';
const ESCAPE = "\\";
const METRIC_SEPARATOR = ",";
const PARAMETER_SEPARATOR = ";";
const VALUE_SEPARATOR = "=";
const DURATION_PARAMETER = "dur";
const DESCRIPTION_PARAMETER = "desc";

/** Corta `text` en `separator` salvo dentro de comillas, respetando los escapes. */
function splitOutsideQuotes(text: string, separator: string): string[] {
  const parts: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text.charAt(index);
    if (quoted && character === ESCAPE) {
      current += character + text.charAt(index + 1);
      index += 1;
    } else if (character === QUOTE) {
      quoted = !quoted;
      current += character;
    } else if (character === separator && !quoted) {
      parts.push(current);
      current = "";
    } else {
      current += character;
    }
  }
  parts.push(current);
  return parts;
}

function unquote(value: string): string | null {
  if (!value.startsWith(QUOTE)) {
    return TOKEN.test(value) ? value : null;
  }
  if (value.length < 2 || !value.endsWith(QUOTE)) {
    return null;
  }
  return value.slice(1, -1).replace(/\\(.)/g, "$1");
}

function parseDuration(value: string | undefined): number | null {
  if (value === undefined) {
    return null;
  }
  const duration = Number(value);
  return Number.isFinite(duration) && duration >= 0 ? duration : null;
}

function parseParameters(parts: readonly string[]): Map<string, string> {
  const parameters = new Map<string, string>();
  for (const part of parts) {
    const separator = part.indexOf(VALUE_SEPARATOR);
    const key = (separator < 0 ? part : part.slice(0, separator)).trim().toLowerCase();
    const value = separator < 0 ? null : unquote(part.slice(separator + 1).trim());
    // La especificación se queda con la primera aparición de cada parámetro.
    if (TOKEN.test(key) && value !== null && !parameters.has(key)) {
      parameters.set(key, value);
    }
  }
  return parameters;
}

function parseMetric(entry: string): TimingMetric | null {
  const [rawName = "", ...rawParameters] = splitOutsideQuotes(entry, PARAMETER_SEPARATOR);
  const name = rawName.trim();
  if (!TOKEN.test(name)) {
    return null;
  }
  const parameters = parseParameters(rawParameters);
  return {
    name,
    durationMs: parseDuration(parameters.get(DURATION_PARAMETER)),
    description: parameters.get(DESCRIPTION_PARAMETER) ?? null,
  };
}

/**
 * Las métricas de la cabecera, en orden. Una métrica repetida se queda con su primera
 * aparición, y una entrada que no es una métrica se descarta.
 */
export function parseServerTiming(header: string | null | undefined): TimingMetric[] {
  if (header === null || header === undefined) {
    return [];
  }
  const metrics = new Map<string, TimingMetric>();
  for (const entry of splitOutsideQuotes(header, METRIC_SEPARATOR)) {
    const metric = parseMetric(entry);
    if (metric !== null && !metrics.has(metric.name)) {
      metrics.set(metric.name, metric);
    }
  }
  return [...metrics.values()];
}

/**
 * Reparte el total entre las etapas con duración. Sin métrica `total`, el total es la suma
 * de las etapas; con ella, lo que sobra queda como tiempo sin atribuir. `null` si ninguna
 * métrica trae duración.
 */
export function stageBreakdown(metrics: readonly TimingMetric[]): StageBreakdown | null {
  const measured = metrics.filter(
    (metric): metric is TimingMetric & { durationMs: number } => metric.durationMs !== null,
  );
  const reportedTotal = measured.find((metric) => metric.name === TOTAL_METRIC)?.durationMs ?? null;
  const stages = measured.filter((metric) => metric.name !== TOTAL_METRIC);
  if (stages.length === 0 && reportedTotal === null) {
    return null;
  }
  const stagesMs = stages.reduce((sum, stage) => sum + stage.durationMs, 0);
  const totalMs = Math.max(reportedTotal ?? stagesMs, stagesMs);
  return {
    stages: stages.map((stage) => ({
      name: stage.name,
      description: stage.description,
      durationMs: stage.durationMs,
      share: totalMs > 0 ? stage.durationMs / totalMs : 0,
    })),
    totalMs,
    unattributedMs: totalMs - stagesMs,
  };
}
