# Product

<!-- impeccable:product-schema 1 -->

> Escrito a partir del enunciado (Anexo A, opción A), del issue #88 y del README del servicio
> (`apps/rag/README.md`). Lo que no sale de esas fuentes está marcado como **supuesto**.

## Platform

web

## Users

- **Quien investiga o estudia bases de datos y recuperación de información**: un alumno del
  curso, un tesista o un profesor que tiene una pregunta concreta («¿cómo estiman la
  cardinalidad los optimizadores aprendidos?») y quiere una respuesta corta que pueda
  **verificar** en el paper, no un resumen que tenga que creer.
- **Quien evalúa el sistema** (el profesor, el jurado de la exposición): quiere ver por qué el
  sistema respondió lo que respondió: qué fragmentos trajo BM25, cuáles los embeddings, cómo
  se fusionaron, el SQL que corrió en el gestor y cuánto costó.
- Situación (**supuesto**): en una laptop, en una sesión de lectura de minutos a horas, a
  menudo en la noche; a veces desde el celular, para una consulta rápida.

## Product Purpose

Responder preguntas sobre ~1 500 papers de arXiv (cs.DB y cs.IR, 2025-2026) y el material del
curso con afirmaciones citadas `[n]`, y llevar cada cita a la página exacta del PDF de donde
sale, con el pasaje a la vista. El éxito es que el lector confíe en una respuesta **porque la
comprobó** en uno o dos clics, y que vea con la misma honestidad cuando el sistema no sabe,
cuando una cita no corresponde a ningún documento o cuando la respuesta quedó incompleta.

## Positioning

La recuperación corre en el minigestor construido en el curso: BM25 sobre su índice
invertido (SPIMI), vecinos sobre su HNSW y la fusión RRF en el servicio. Ningún chat genérico
puede mostrar el SQL exacto de cada búsqueda, el puesto de cada fragmento en cada método y la
página del PDF que respalda cada frase, todo en la misma pantalla.

## Operating Context

- Servicio `papers-api` (FastAPI) detrás del nginx de `papers-web`, en el puerto 8103; la
  interfaz solo habla con `/api`.
- `POST /search` (bm25, vector, hybrid), `POST /answers` en Server-Sent Events,
  `GET /papers/{id}/pages/{n}` y `GET /papers/{id}/file` (PDF con `Range`), `GET /facets`.
- El modelo es Claude Sonnet 5.5; en desarrollo y en la CI, `RAG_LLM_PROVIDER=simulated`.
  Sin clave, `/answers` responde 503 y la búsqueda sigue funcionando.
- Tope diario de gasto (429 `BudgetExhaustedError`) y límite por IP de nginx (429).

## Capabilities and Constraints

- Los papers están en inglés; las preguntas pueden ser en español o inglés (los embeddings
  son multilingües, BM25 no).
- Una respuesta usa hasta 8 fragmentos; cada uno es de una o dos páginas de un paper.
- La CSP del nginx no permite `<iframe>` ni `<object>` del API (`frame-ancestors 'none'`,
  `object-src 'none'`) ni estilos en línea: el PDF se dibuja en la página.
- Sin cuentas ni historial en el servidor: la conversación vive en la pestaña.

## Brand Commitments

- Nombre del producto: **Papers** (el del servicio: `papers-api`, `papers-web`).
- Interfaz en español, como el resto del proyecto; los papers, en su idioma.
- Producto aparte: nada visual de la consola del gestor ni de las otras tres apps.

## Evidence on Hand

- El corpus real (`data/datasets/rag_papers/`, no versionado) y el gestor sembrado.
- El LLM simulado, determinista, para desarrollo, tests y capturas. **No hay respuestas
  reales de Claude todavía** (no hay clave): ninguna captura ni texto debe presentarlas como
  tales.

## Product Principles

1. **Verificable antes que fluido**: cada afirmación lleva a su página en un clic.
2. **Honestidad de estado**: sin resultados, sin modelo, sin presupuesto, citas inválidas y
   respuestas cortadas se dicen tal cual, con lo que el lector puede hacer.
3. **La máquina a la vista**: el SQL, los puestos por método, los tiempos, los tokens y el
   costo están a un gesto, nunca en el camino de la lectura.
4. **Lectura larga primero**: tipografía y medida pensadas para leer respuestas y pasajes.

## Accessibility & Inclusion

WCAG 2.2 AA: contraste, foco visible, todo por teclado (incluido saltar de una cita al PDF y
volver), `prefers-reduced-motion`, tema claro y oscuro, y uso real a 390 px.
