// Una prueba de carga de k6 de un escenario de `load_benchmark.py`: VUS usuarios virtuales que
// piden sin pausa durante DURATION (modelo cerrado). Cada iteración toma la petición siguiente
// del archivo del escenario, así que una lista de claves distintas mantiene la caché fría.
//
// El escenario (/fixtures/<SCENARIO>.json) trae `method`, `requests` (cada una con `path` y,
// si es JSON, `body`) y, para subir un archivo, `upload` con su campo, nombre, tipo y archivo.
// De cada respuesta se guardan las etapas de `Server-Timing` que pide STAGES y la cabecera
// `X-Cache`, si viene. El resumen queda en /out/<SCENARIO>.json.
import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL;
const SCENARIO = __ENV.SCENARIO;
const STAGES = (__ENV.STAGES || '').split(',').filter((stage) => stage.length > 0);
const scenario = JSON.parse(open(`/fixtures/${SCENARIO}.json`));
const requests = new SharedArray('requests', () => scenario.requests);
const upload = scenario.upload ? open(`/fixtures/${scenario.upload.file}`, 'b') : null;
const stageTrends = Object.fromEntries(
  STAGES.map((stage, position) => [stage, new Trend(`stage_${position}`, true)]),
);
const cacheResults = new Counter('cache_results');

export const options = {
  scenarios: {
    load: { executor: 'constant-vus', vus: Number(__ENV.VUS), duration: __ENV.DURATION },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
  discardResponseBodies: true,
  // La prueba mide, no aprueba ni reprueba: estos umbrales siempre se cumplen y solo sirven
  // para que el resumen cuente aparte los aciertos y los fallos de la caché.
  thresholds: {
    'cache_results{result:hit}': ['count>=0'],
    'cache_results{result:miss}': ['count>=0'],
  },
};

function send(request) {
  const url = `${BASE_URL}${request.path}`;
  if (upload !== null) {
    const body = {};
    body[scenario.upload.field] = http.file(upload, scenario.upload.name, scenario.upload.type);
    return http.post(url, body);
  }
  if (scenario.method === 'POST') {
    return http.post(url, JSON.stringify(request.body), {
      headers: { 'Content-Type': 'application/json' },
    });
  }
  return http.get(url);
}

function recordStages(response) {
  const timing = response.headers['Server-Timing'] || '';
  for (const part of timing.split(',')) {
    const [name, duration] = part.trim().split(';dur=');
    if (name in stageTrends) {
      stageTrends[name].add(Number(duration));
    }
  }
}

export default function () {
  const request = requests[exec.scenario.iterationInTest % requests.length];
  const response = send(request);
  recordStages(response);
  const cache = response.headers['X-Cache'];
  if (cache) {
    cacheResults.add(1, { result: cache });
  }
}

export function handleSummary(data) {
  const stages = Object.fromEntries(
    STAGES.map((stage, position) => [stage, data.metrics[`stage_${position}`] || null]),
  );
  const out = { metrics: data.metrics, stages, state: data.state };
  return { [`/out/${SCENARIO}.json`]: JSON.stringify(out) };
}
