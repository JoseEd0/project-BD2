# Observabilidad: Prometheus y Grafana

El perfil `observability` del [`compose.yaml`](../../compose.yaml) raíz suma dos servicios al
despliegue:

```bash
docker compose --profile observability up -d
docker compose --profile observability up -d --scale shop-api=4   # las réplicas aparecen solas
```

- **`prometheus`** ([`prometheus.yml`](prometheus.yml)) lee `GET /metrics` cada 5 s del gestor
  y de cada réplica de cada API. Encuentra las réplicas por el DNS de Docker
  (`dns_sd_configs`), que con `--scale` devuelve una dirección por réplica, y vuelve a
  preguntar cada 5 s. Está en la red interna del gestor y en la de cada app, solo para leer;
  no publica ningún puerto. Guarda las series en memoria (`PROMETHEUS_RETENTION`, 6 h por
  omisión): es el tablero de una demo, no un histórico.
- **`grafana`** abre en `http://localhost:8300` (`GRAFANA_PORT`, solo en localhost) el tablero
  [`minigestor.json`](grafana/dashboards/minigestor.json), versionado y de solo lectura, con
  Prometheus como fuente. Entra quien abre la página, como lector: no hay contraseña que
  guardar.

| Panel | Consulta |
|---|---|
| Réplicas | `count by (job) (up == 1)` |
| Peticiones por segundo, por servicio y ruta | `rate(http_requests_total[1m])` |
| Latencia p95 por ruta; p50 y p99 por servicio | `histogram_quantile` sobre `http_request_duration_seconds_bucket` |
| Errores por segundo (4xx y 5xx) | `http_requests_total{status=~"[45].."}` |
| Aciertos de la caché | `cache_lookups_total{result="hit"}` sobre el total |
| CPU por servicio (núcleos) | `rate(process_cpu_seconds_total[1m])` |
| Memoria residente por réplica | `process_resident_memory_bytes` |

Las métricas las exportan el gestor ([`src/api/metrics.py`](../../src/api/metrics.py)) y el
kit de los servicios ([`appkit.metrics`](../../apps/kit/src/appkit/metrics.py)), con los
mismos nombres; qué cuenta cada una está en el
[README del kit](../../apps/kit/README.md#métricas). Ningún nginx reenvía `/api/metrics`.

`tests/deploy/test_observability_config.py` comprueba sin Docker que cada nombre que lee
Prometheus es un servicio en una red a la que está conectado y que el tablero solo pide
métricas que se exportan; la CI corre además `promtool check config`.

La prueba de carga con 1, 2 y 4 réplicas que se mira en este tablero está en la
[sección 21 de los benchmarks](../../benchmarks/README.md#21-prueba-de-carga--réplicas-de-cada-api-frente-a-un-solo-gestor).
