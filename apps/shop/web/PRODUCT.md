# Product
<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Quien busca un instrumento o un accesorio en Lima y quiere saber **qué más le sirve y dónde
lo encuentra cerca**: el guitarrista que va a comprar cuerdas y un cable, la baterista que
mira baquetas, el que arma su primer home studio. Llega con algo en mente (unas palabras,
una foto de un pedal que vio en un ensayo) y termina en la ficha de un producto, mirando qué
compraron con él otros clientes y en qué tienda cercana hay stock.

También lo usa el alumno del curso en la exposición para mostrar las cuatro clases de datos
del gestor trabajando juntas en una recomendación: el SQL de cada señal, sus tiempos y la
caché. *(Inferido del enunciado y de #83, sin entrevista: este archivo se escribió desde el
brief del issue.)*

## Product Purpose

**Cajón** es la tienda de instrumentos de la opción B: catálogo, ficha de producto y compra
sobre el API de la tienda (`apps/shop`), que a su vez solo habla con el gestor por su API.
Busca por palabras (BM25 sobre el invertido en inglés) o por foto (CLIP), filtra por
departamento y precio, y en cada ficha recomienda con cuatro señales —compras conjuntas,
parecido visual, descripción y stock cercano— y una fila fundida, cada recomendación con su
motivo en una frase. El éxito es que quien mira entienda **por qué** se le recomienda algo
en un vistazo, y que al simular una compra vea cambiar sus recomendaciones.

## Positioning

Una tienda donde cada recomendación dice de dónde salió: «comprado junto con este producto
por 12 clientes», «se ve parecido», «en stock a 1,2 km en Real Plaza». Detrás de cada fila
está una consulta del gestor (relacional, vectorial, texto y espacial) que se puede leer tal
como corrió, con sus parámetros y sus milisegundos.

## Operating Context

- Catálogo real: 24 587 productos de Amazon Reviews 2023, categoría Musical Instruments; las
  fichas (título, marca, viñetas, descripción) están **en inglés**, la interfaz en español.
- Precios en dólares de Amazon (`US$`); 8 105 productos no tienen precio y así se muestra.
- 209 tiendas reales de Lima (OpenStreetMap) con inventario sintético; la ubicación del
  usuario la da el navegador (con permiso) o se elige en el mapa; por omisión, el centro de
  Lima.
- Los usuarios son los del dataset (57 439, ids anónimos de Amazon): se elige uno para ver
  «recomendado para ti».
- Rutas del API: ficha, búsqueda de texto, de fotos por texto y por foto subida, tiendas
  cercanas y con stock de un producto, recomendaciones por producto y por usuario, compras.
  Cada recomendación trae `senales[]` con su `sql` y `parametros`, `X-Cache` y
  `Server-Timing`.
- Se usa en el escritorio y en el celular (la foto desde la cámara), y se proyecta en la
  exposición.

## Capabilities and Constraints

- Una compra es simulada: registra la compra en el gestor e invalida la caché; no hay pago,
  carrito ni cuentas.
- Un producto sin compras conjuntas es un **arranque en frío**: sus pesos pasan a imagen y
  texto, y la interfaz lo explica.
- La foto subida se manda solo al API de la tienda; JPEG, PNG o WebP, con su tamaño máximo.
- Errores con motivo: servicio caído (`/health`), ubicación denegada, foto ilegible o
  demasiado grande, búsqueda vacía, consulta booleana mal formada.

## Brand Commitments

- Nombre: **Cajón** — el cajón peruano, y el cajón donde se guarda lo que se encuentra.
  Subtítulo «instrumentos en Lima».
- Una app aparte, con identidad propia: nada visual de las otras apps de la Parte 5
  (partitura marcada, fotomatón, microficha diazo) ni de la consola del gestor.

## Evidence on Hand

- Las fotos de los productos (512 px, JPEG, fondo blanco en su mayoría) y las fichas reales.
- Las cifras medidas del recomendador (`apps/shop/README.md`, §16 de `benchmarks/README.md`).
- No se inventan reseñas, ofertas, descuentos, envíos ni precios en soles.

## Product Principles

1. Cada recomendación se explica: motivo en palabras, señal de origen y su SQL a un clic.
2. Lo cercano es concreto: tienda con nombre, distancia y unidades, en el mapa.
3. El producto manda: la foto y el título primero; lo técnico, callado y desplegable.
4. Cada estado tiene salida: sin resultados, sin recomendaciones, sin ubicación, sin servicio.

## Accessibility & Inclusion

WCAG 2.2 AA: contraste, todo usable con teclado (filas de recomendaciones, mapa con su lista
equivalente), foco visible, movimiento reducido, 390 px sin desplazamiento horizontal. La
señal de una recomendación no se distingue solo por color: lleva su nombre.
