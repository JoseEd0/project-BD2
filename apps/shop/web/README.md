# Cajón — la interfaz de la tienda

La interfaz propia de la opción B de la Parte 5: una tienda de instrumentos en Lima que dice
por qué recomienda cada cosa. Se busca por palabras, por cómo se ve o con una foto, se abre
la ficha de un producto y ahí se lee, fila por fila, qué propuso cada señal del recomendador
—compras conjuntas, parecido visual, descripción y stock cercano— y la fila que las funde,
cada recomendación con su sello y su frase. Qué es y para quién está en
[`PRODUCT.md`](PRODUCT.md); cómo se ve y por qué (la etiqueta colgante y el sello), en
[`DESIGN.md`](DESIGN.md).

Responde en el puerto 8102 (`shop-web` en [`../compose.yaml`](../compose.yaml)); su nginx
reenvía `/api` solo a `shop-api` ([`../README.md`](../README.md)).

| Carpeta | Qué hay |
|---|---|
| `src/api/` | el cliente tipado del contrato (`openapi/shop.json`), las consultas de React Query, las cabeceras (`X-Cache`, `Server-Timing`) y los errores del API en palabras de la tienda |
| `src/catalog/` | los filtros (departamento y precio) en la URL, los modos de búsqueda y las sugerencias traducidas |
| `src/recommend/` | lógica pura: las filas por señal a partir de la lista fundida, los sellos, lo que cambió tras una compra y el SQL legible |
| `src/product/` | la ficha: la descripción en viñetas, comprar y el panel de stock |
| `src/location/` | dónde está quien compra (navegador, mapa o el centro de Lima) y el mapa de Leaflet |
| `src/photo/` | la foto para buscar: se revisa y se reduce en el navegador antes de subirla |
| `src/components/` | la barra, el producto con su etiqueta, las filas, los sellos, el SQL y los tiempos |

## Las pantallas

- **Catálogo** (`/`): sin búsqueda, sugerencias en español que buscan en inglés (las fichas
  de Amazon están en inglés) y «Recomendado para ti» del cliente elegido. Con búsqueda, la
  rejilla de resultados con su puntaje (BM25 o coseno) y los filtros a la izquierda (un
  desplegable en el celular). La búsqueda y los filtros viven en la URL: se comparten y
  «Atrás» funciona.
- **Ficha** (`/producto/:id`): foto, etiqueta (precio, marca, valoración, n.º de catálogo),
  «Comprar», la descripción, el mapa de stock y las filas de recomendaciones.
- **Para ti** (`/para-ti`): lo que compró el cliente y sus recomendaciones señal por señal.

## Las filas de cada señal

El API devuelve la lista fundida, y cada recomendación trae un motivo por señal que la
propuso, con lo que aportó (`motivos[].senal`, `aporte`, `texto`). La fila de una señal son
las recomendaciones que tienen un motivo de esa señal, ordenadas por lo que ella aportó
([`recommend/rails.ts`](src/recommend/rails.ts)). Por eso se piden 24 recomendaciones: cada
señal tiene de dónde llenar su fila. Una fila vacía dice por qué (sin foto, sin compras
conjuntas, sin stock en el radio), y una señal que no corrió lo dice junto a su nombre. Cada
fila muestra su SQL exacto con sus parámetros (`senales[].sql`, `parametros`), cuántos
candidatos dio y cuánto tardó.

## Comprar y ver el cambio

«Comprar» está aparte (es lo único que cambia datos) y pide antes un cliente. Al comprar se
guarda cómo estaban las recomendaciones, `POST /usuarios/{id}/compras` registra la compra e
invalida la caché del cliente, y React Query vuelve a pedir todo lo que cuelga de
`recomendaciones`. La ficha muestra entonces «Recomendado para ti, después de la compra», con
un sello `NUEVO` en lo que no estaba antes y la cabecera `X-Cache` de la respuesta nueva
(`fallo`: se calculó otra vez en el gestor).

## El mapa

Leaflet con las teselas de OpenStreetMap (`VITE_MAP_TILES_URL`; su origen va en el `img-src`
de la CSP con `WEB_IMG_SOURCES`), en su propio fragmento del bundle. Las tiendas con stock van
numeradas como en la lista de al lado, que es su equivalente con el teclado y el lector de
pantalla. La ubicación sale del navegador si se da permiso (`geolocation=(self)` en la
`Permissions-Policy`), de un clic en el mapa o, por omisión, de la Plaza Mayor de Lima.

## La foto

`Subir foto` y `Tomar foto` (la cámara del celular con `capture`, sin pedir permisos de
cámara a la página). La foto se gira según su EXIF y se reduce a 1 024 px en JPEG en el
navegador antes de subirla (`photo/prepare.ts`). Lo que no es imagen, pesa más de 25 MB o el
navegador no puede abrir se avisa sin subirlo; lo que el API rechaza
(`UnreadablePhotoError`, `UploadTooLargeError`, `TooManyPixelsError`) se explica con su
motivo.

## Comandos

```bash
npm ci
npm run dev          # http://localhost:5173, /api reenviado a WEB_DEV_API_URL
npm run build        # tipos (tsc -b) y bundle con el service worker
npm test             # Vitest
npm run e2e          # Playwright contra el build, con el API simulado (e2e/fakeShop.ts)
npm run contracts    # vuelve a exportar openapi/shop.json y regenera los tipos
```

Los tests de Vitest cubren las filas por señal, los sellos y las frases, los filtros, los
errores del API, la foto antes de subirla y las pantallas con el API simulado. Los de
Playwright recorren buscar, abrir un producto, comprar y ver cambiar las recomendaciones; la
ubicación denegada, una foto que el API no lee, los 390 px sin desplazamiento lateral y la
PWA. El API de verdad lo prueba el humo del compose en la CI.
