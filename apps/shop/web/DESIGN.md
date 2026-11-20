# Cajón — diseño

Una tienda de instrumentos en Lima que dice por qué recomienda cada cosa. Es una app de
trabajo (modo *operar*): buscar, abrir una ficha, leer las recomendaciones, ver dónde hay
stock y comprar. Los tokens viven en [`src/styles/tokens.css`](src/styles/tokens.css); este
documento dice por qué.

## Vitrina (#162)

Desde #162 el catálogo se lee como una tienda grande: tarjetas blancas con la foto arriba,
marca, título en dos líneas, estrellas con su número de opiniones y el precio grande; la
ficha lleva una caja de compra aparte. La barra es pizarra (`--tolex` `#131A22`), el acento
es la madera del cajón (`--accent` `#B4501A`, en `src/styles/storefront.css`) y las estrellas,
ámbar. La etiqueta manila se retiró: `--tag` es ahora un gris neutro que solo sostiene los
sellos de las recomendaciones, que siguen siendo el gesto propio. En el mapa, cada tienda es
un alfiler blanco con su número en el acento (relleno cuando está elegida, igual que en la
lista) y quien compra, un punto azul con halo. Lo que sigue describe el diseño anterior en lo
que no contradiga esto.

## La idea: la etiqueta colgante y el sello

En una tienda de instrumentos (las de Jr. Paruro, en el centro de Lima, o cualquier tienda
de música) cada guitarra y cada pedal cuelga con **su etiqueta de cartulina manila**: un
agujero con su pita, el modelo escrito a mano y el precio sellado. Eso es el producto en la
pantalla: la foto y, colgada de ella, la etiqueta con el precio, el título y la marca.

El gesto propio es **el sello**: cada recomendación lleva en su etiqueta un sello de goma por
cada señal que la propuso —`COMPRADO JUNTO`, `SE VE PARECIDO`, `DESCRIPCIÓN`, `EN STOCK
CERCA`—, cada señal con su tinta, y debajo la frase que lo explica («comprado junto con este
producto por 12 clientes»). Una recomendación de la fila fundida lleva varios sellos, del que
más aportó al que menos. Las tintas son la única serie de datos de la app y no se usan para
nada más.

Se rechaza a propósito la rejilla de tarjetas idénticas con estrellas y un botón naranja: la
etiqueta es lo que se lee, y el motivo va escrito en ella.

## Escena y tema

Quien compra mira desde el celular en la noche o desde una laptop en el día; en la
exposición se proyecta. Las fotos de Amazon vienen sobre **blanco**: viven siempre en un
recuadro blanco, también en el tema oscuro (como el producto bajo la luz de la vitrina). El
tema sigue al sistema y se puede fijar (sistema, claro, oscuro).

## Color

Estrategia: **comprometida en el armazón y en las etiquetas, contenida en el trabajo**. La
barra superior es negro *tolex* (el vinilo que forra amplificadores y estuches); el suelo es
un gris frío de tienda; las etiquetas, manila; las tintas de sello, cuatro.

| Token | Claro | Oscuro | Para qué |
|---|---|---|---|
| `--floor` | `#F2F3F1` | `#111214` | fondo |
| `--shelf` | `#FFFFFF` | `#1A1C1F` | paneles, campos, recuadro de foto (blanco en los dos) |
| `--ink` | `#16171A` | `#ECECE8` | texto |
| `--ink-soft` | `#454850` | `#BCBEC4` | texto secundario |
| `--ink-faint` | `#5E626B` | `#9EA2AA` | rótulos y metadatos (≥ 4,5:1 sobre `--floor` y `--shelf`) |
| `--rule` | `#D7D9DC` | `#2D3035` | filetes de 1 px |
| `--tolex` | `#151515` | `#09090A` | barra superior |
| `--on-tolex` | `#F3F1EA` | `#F3F1EA` | texto sobre la barra |
| `--tag` | `#EEDDA9` | `#D9C88E` | la etiqueta manila (un poco apagada en oscuro, sin deslumbrar) |
| `--tag-ink` | `#2A2313` | `#231D0F` | texto sobre la etiqueta, en los dos temas |
| `--focus` | `#0B57D0` | `#8AB4F8` | anillo de foco de 3 px |
| `--alert` | `#B42318` | `#FF9C8F` | errores, servicio caído |

**Tintas de sello** (texto y borde del sello, siempre sobre manila; ≥ 4,5:1 en los dos temas):
compras violeta `#5B2A86`, visual azul `#1D4E89`, texto verde `#1F5A34`, cercanía rojo
`#922619`. Fuera de la etiqueta (la cabecera de cada fila, los marcadores del mapa) cada
tinta tiene su versión clara para el tema oscuro: `#C9A8EE`, `#90B9F0`, `#8AD3A0`, `#F49D91`.
**Nunca solo color**: cada sello dice el nombre de su señal.

## Tipografía

**Archivo** (variable, con eje de ancho), auto-hospedada con `@fontsource-variable` porque la
CSP solo admite fuentes del mismo origen: una grotesca de cartel de tienda que en ancho normal
es una buena letra de interfaz y **condensada** es la letra de un sello o de una etiqueta de
precio. Monoespaciada del sistema solo para el SQL y el número de catálogo (el ASIN).

| Paso | Tamaño | Uso |
|---|---|---|
| `--text-xs` | 0,8125 rem | sellos, rótulos, metadatos |
| `--text-sm` | 0,9375 rem | etiquetas, motivos, tablas |
| `--text-md` | 1 rem | cuerpo |
| `--text-lg` | 1,25 rem | títulos de fila y de panel |
| `--text-xl` | 1,5 rem | título del producto |
| `--text-price` | 1,375 rem en la etiqueta, 2 rem en la ficha | precio, condensado 75 y 700 |

Sellos y precios: ancho 75, peso 700, mayúsculas con 0,04 em de espaciado, cifras tabulares.
Escala fija en rem; interlineado 1,5 en el cuerpo y 1,25 en títulos. Los títulos de Amazon son
larguísimos: tres líneas en una etiqueta, completos en la ficha.

## Espacio y forma

Base de 4 px: `--space-1` 4 · `--space-2` 8 · `--space-3` 12 · `--space-4` 16 · `--space-6`
24 · `--space-8` 32 · `--space-12` 48. Más aire sobre un título que debajo. Paneles separados
por filetes de 1 px, sin sombras; la única sombra es la de la etiqueta, que cuelga
(`0 1px 2px` y `0 4px 10px` suaves). La etiqueta tiene las dos esquinas de arriba cortadas a
45° y su agujero con ojal; el sello, 2 px de radio y borde de 1,5 px, girado −2° (un sello
nunca cae derecho). Objetivos táctiles de 44 px.

## Movimiento

- Estado, no decoración: 160 ms, `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- **Un solo momento**: después de una compra, las recomendaciones que cambiaron se
  **re-sellan** en su lugar —el casillero no se mueve, cambia lo que tiene— y llevan un sello
  `NUEVO` que cae con una escala de 1,15 a 1 y un golpe de opacidad (220 ms).
- `prefers-reduced-motion: reduce`: sin transiciones ni el golpe del sello; el sello `NUEVO`
  sigue ahí, porque dice qué cambió.

## Componentes

- **Barra superior** (tolex): «Cajón» con «instrumentos en Lima», el buscador (palabras, cómo
  se ve, foto), el cliente elegido y el tema. En 390 px el buscador baja a su propia fila.
- **Buscador**: un campo con tres modos en pestañas —*Palabras* (BM25), *Cómo se ve* (CLIP de
  texto a foto) y *Foto* (subir o la cámara del celular)—. El catálogo está en inglés y el
  campo lo dice; las sugerencias muestran la palabra en español y buscan en inglés.
- **Filtros**: columna izquierda en escritorio, desplegable «Filtros» en el celular:
  departamento (lista con conteo) y precio (tramos y mínimo/máximo en US$). Un producto sin
  precio no pasa un filtro de precio, y se dice.
- **Producto en lista**: recuadro blanco con la foto y la etiqueta colgada debajo; en una
  recomendación, con sus sellos y su frase. Todo el producto es un enlace.
- **Fila de recomendaciones**: título con el sello de su señal, cuántos candidatos dio y en
  cuántos ms, y «Ver SQL» (desplegable con la consulta exacta y sus parámetros); los
  productos en una fila con desplazamiento horizontal y pestañas anterior/siguiente de
  tolex (solo con ratón; con el teclado se recorren los enlaces).
- **Ficha**: a la izquierda, la foto y su etiqueta grande colgada debajo (precio primero,
  título, marca, valoración, n.º de catálogo) y la descripción en viñetas, plegada; a la
  derecha, «Comprar» aislado de lo demás (es la acción con consecuencias) y el mapa de stock,
  visibles sin desplazarse en 1440 x 900. En el celular, uno debajo del otro. Debajo, las filas.
- **Mapa de stock** (Leaflet): tiendas con el producto, un punto por tienda con su número y la
  lista equivalente debajo (nombre, rubro, distancia, unidades); la ubicación del usuario y
  el radio de cercanía; «Usar mi ubicación» y «Elegir en el mapa». El encuadre se ajusta a las
  tiendas (hasta zoom 16) y, en el tema oscuro, las teselas se apagan y desaturan.
- **Tiempos y caché**: un desplegable callado con la barra apilada por etapa y su tabla; la
  caché como una palabra al lado (`caché: acierto`, `fallo`, `sin caché`).
- **Estados**: cada vacío dice por qué y qué probar (buscar en inglés, quitar filtros, cambiar
  de modo); el arranque en frío se explica arriba de las filas; servicio caído con
  «Reintentar»; ubicación denegada con el punto por omisión y el selector del mapa.

## Lo que no se hace

Sin degradados, sin cristal, sin estrellas de colores ni insignias de oferta, sin emojis como
iconos (iconos SVG propios de trazo 1,75 px), sin rótulos encima de los títulos, sin tintas de
sello fuera de las señales, sin modales para tareas que caben en la página.
