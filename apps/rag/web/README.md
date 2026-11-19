# Papers: la interfaz del RAG

La interfaz propia del servicio de preguntas a papers ([`apps/rag`](../README.md)): una
conversación con respuestas citadas y, al lado, el PDF abierto en la página de cada cita con
el pasaje marcado. Qué es y para quién, en [`PRODUCT.md`](PRODUCT.md); cómo se ve y por qué,
en [`DESIGN.md`](DESIGN.md). Está hecha sobre el kit web ([`apps/web-kit`](../../web-kit/README.md))
y se sirve en `papers-web` (`:8103`), cuyo nginx reenvía `/api` solo a `papers-api`.

| Parte | Qué hace | Del API |
|---|---|---|
| Conversación | la respuesta en streaming; cada `[n]` es una llave que abre su fragmento | `POST /answers` (SSE) |
| Filtros | categorías y años del corpus, que van al `WHERE` de cada búsqueda | `GET /facets`, `filters` |
| Página | el paper en la página citada, dibujado con pdf.js, con el pasaje marcado y la ficha de sus páginas | `GET /papers/{id}/pages/{n}`, `GET /papers/{id}/file` |
| Recuperación | lo que trajo BM25, los embeddings y la fusión RRF, con el SQL de cada consulta y sus tiempos | `POST /search` (`queries`, `Server-Timing`) |
| Tiempos y costo | el `Server-Timing` de la recuperación, el tiempo hasta el primer texto y total, los tokens y el costo | evento `done` |

## Cómo funciona

- **El stream.** `EventSource` solo hace GET, y `/answers` es un POST: `research/streamAnswer.ts`
  lee el cuerpo de `fetch` con un `ReadableStream`, decodifica UTF-8 por trozos y se lo pasa a
  `research/sse.ts`, un lector de `text/event-stream` (WHATWG) que soporta trozos que cortan
  una línea, un `\r\n` o un carácter de varios bytes. Un error antes del stream es el JSON del
  kit (`kind`); a mitad, un evento `error`; un cuerpo que se cierra sin `done`, una conexión
  cortada.
- **Los estados** de cada pregunta (`research/turns.ts`): buscando → escribiendo → respondida,
  fallida o detenida, con los tiempos medidos en el navegador. `research/failures.ts` lleva
  cada error a una categoría que la interfaz explica (sin clave del modelo `503`, tope diario
  `429 BudgetExhaustedError`, límite de nginx `429`, modelo caído, gestor caído, red,
  conexión cortada…) y dice si reintentar tiene sentido.
- **Las citas** se cortan con la misma expresión que usa el servicio para comprobarlas
  (`research/citations.ts`): `[2]`, `[1][3]` y `[1, 3]`. Las que no corresponden a ningún
  fragmento enviado se tachan y se explican, no se esconden.
- **El PDF** (`reader/`): pdf.js, cargado solo al abrir la primera cita, pide el archivo por
  rangos y dibuja la página con su capa de texto; `reader/passage.ts` ubica el pasaje del
  corpus en ese texto. Por qué no el visor del navegador en un `<iframe>`: en `DESIGN.md`, «El
  PDF».

## Comandos

```bash
npm ci
npm run dev        # http://localhost:5173, /api reenviado a WEB_DEV_API_URL
npm run build      # tipos (tsc -b) y bundle con el service worker
npm test           # Vitest
npm run e2e        # Playwright contra el build (la primera vez, npx playwright install chromium)
npm run contracts  # vuelve a exportar openapi/rag.json y genera src/api/generated/rag.ts
```

Para desarrollar contra datos reales: el gestor sembrado y el API con el modelo simulado
(`RAG_LLM_PROVIDER=simulated`, sin clave ni costo), y `WEB_DEV_API_URL` apuntando a ese API
(ver `.env.example` y [`apps/rag/README.md`](../README.md)).

Los tests de Vitest cubren el lector SSE (cortes en cada posición, `\r\n` partido, UTF-8
partido), las citas, la máquina de estados, el mapeo de errores, la ubicación del pasaje y la
pantalla con cada estado. Los de Playwright corren contra el build con el API simulado en
rutas: preguntar, ver el stream, tocar `[1]` y comprobar que el PDF (uno generado en el test)
se abre en su página con el pasaje marcado; la hoja a 390 px; el aviso sin clave del modelo;
el tema antes de cargar y el shell sin conexión.
