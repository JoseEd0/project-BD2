# Songcheck — la interfaz de copyright en audio

La interfaz propia de la opción C de la Parte 5: un estudio de escucha. Se sube un audio y
se ve **dónde** coincide con el catálogo: la forma de onda grabada sobre un pentagrama, cada
tramo coincidente como un trazo de resaltador del color de su canción original (con su
letra, su parecido y su desfase) y, al elegirlo, el pentagrama del original unido al mismo
pasaje. Debajo, la escucha A/B y una conclusión en lenguaje claro. Qué es y para quién está
en [`PRODUCT.md`](PRODUCT.md); cómo se ve y por qué, en [`DESIGN.md`](DESIGN.md).

Responde en el puerto 8101 (`copyright-web` en [`../compose.yaml`](../compose.yaml)); su nginx
reenvía `/api` solo a `copyright-api` ([`../README.md`](../README.md)).

| Carpeta | Qué hay |
|---|---|
| `src/api/` | el cliente tipado del contrato (`openapi/copyright.json`), la subida con progreso (`XMLHttpRequest`) y las canciones |
| `src/analysis/` | lógica pura: la conclusión, los tramos sobre la línea de tiempo, la regla, los errores del API y las etapas |
| `src/audio/` | la forma de onda: `decodeAudioData` del navegador y su reducción a picos |
| `src/timeline/` | el pentagrama, los trazos, la llave y el pentagrama del original |
| `src/listening/` | la escucha A/B |
| `src/studio/` | el flujo: subir, cancelar, problemas, tabla de coincidencias y tiempos |

## El flujo

1. El archivo elegido o arrastrado se **decodifica en el navegador** (Web Audio) para dibujar
   su forma de onda, y a la vez se sube a `POST /api/detections` con `XMLHttpRequest`, el
   único que informa el progreso de una subida; «Cancelar» la aborta.
2. La respuesta pone las coincidencias sobre la línea de tiempo: una letra y un color por
   canción, carriles si dos tramos se solapan, el trazo rayado y con borde discontinuo si
   queda bajo el umbral («débil»), nunca solo el color.
3. Los motivos de rechazo son los del API (`415`, `400`, `413` y su mensaje); un `413` de
   nginx, que no trae JSON, se explica con los límites. Sin respuesta, o con `502` a `504`,
   el servicio no está; `/health` (el kit) lo dice en la cabecera y deshabilita la subida.
4. `Server-Timing` da las etapas del servicio. `segments` ya contiene las tres del gestor
   (`decode`, `extraction`, `search`), así que se muestran dentro de ella, desde
   `engine_timings` del cuerpo, y no se suman otra vez al total.

## La escucha A/B

Dos `<audio>`: A es el archivo subido (una URL `blob:`, no vuelve a la red) y B el original
por `GET /api/songs/{id}/audio`, que el servicio reenvía del gestor con `Range`, así que el
reproductor salta al tramo sin bajar la pista entera. Al elegir un tramo, A se pone en su
inicio en la subida y B en su inicio en el original. **Los dos suenan a la vez y solo uno se
oye** (el otro va en silencio): alternar no corta ni salta, como una caja A/B de estudio.
Cada cuadro, si el lado que suena sale del tramo, los dos vuelven a su inicio (bucle), y si
el lado mudo se separó más de 0,12 s, se realinea. Teclas: A y B eligen, X alterna, Espacio
reproduce o pausa; los trazos se recorren con las flechas.

## Comandos

```bash
npm ci
npm run dev        # http://localhost:5173, /api reenviado a WEB_DEV_API_URL
npm run build
npm test           # Vitest: conclusión, tramos, picos, errores, etapas, sincronía A/B
npm run e2e        # Playwright contra el build, con el API simulado (page.route)
WEB_SCREENSHOTS_DIR=/ruta npm run e2e -- screenshots   # capturas claro/oscuro, 1440 y 390 px
```

El test de Playwright sube un remix del catálogo de muestra (`songcheck.sample`): melodías de
notas al azar generadas en el test, 15 s de la 2 y 12,5 s de la 3. El API se simula, pero el
audio es de verdad: el navegador lo decodifica, dibuja su forma de onda y lo reproduce. El
humo del compose (`.github/workflows/ci.yml`) prueba la detección contra el API real.
