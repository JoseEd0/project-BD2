# Songcheck — diseño

Un estudio de escucha para decidir si un audio copia canciones del catálogo. Una sola
pantalla de trabajo (modo *operar*): subir, leer dónde coincide, escuchar A/B, decidir. Los
tokens viven en [`src/styles/tokens.css`](src/styles/tokens.css); este documento dice por qué.

## La idea: una partitura marcada

Cuando un musicólogo forense prepara la prueba de un plagio, pone las dos partituras una
sobre otra y pasa un resaltador por los compases que coinciden, con un color por obra. Eso es
la pantalla: la forma de onda de la subida va grabada sobre un pentagrama tenue, con una
barra de compás en cada salto de la detección (2,5 s), y **cada tramo coincidente es un trazo
de resaltador** del color de su canción original, con la punta biselada de un marcador. Al
elegir un trazo, una llave lo une con el pentagrama del original, debajo, en su propio
instante. Es el único elemento memorable; todo lo demás es callado.

Se rechaza a propósito la tarjeta de resultado con una insignia de confianza: la evidencia
es *dónde* y se escucha.

## Pantalla

Dos columnas: a la izquierda **el catálogo** (buscar por título o artista, escuchar cada
canción y «Buscar parecidas»), a la derecha **la mesa**: el reproductor del audio elegido
(reproducir, ±5 s, posición) con los tramos coincidentes marcados sobre su barra, el
veredicto, la partitura marcada y la tabla de coincidencias, cuyas letras suenan al clic. Un solo audio suena a la vez. Mientras el
catálogo carga o está ocupado se dice así, nunca con el error del gestor.

## Color

Estrategia: **comprometida en el marco, contenida en el trabajo**. La banda de cabecera es
índigo de edición y es también el color de toda acción (reproducir, buscar parecidas), el
fondo es papel lila muy claro y la tinta casi negra violácea. Los colores de resaltador son **las series de
datos y nada más**: una canción original, un color; no se usan como acento ni decoración.

| Token | Claro | Oscuro | Para qué |
|---|---|---|---|
| `--paper` | `#F5F4F8` | `#16131F` | fondo |
| `--paper-raised` | `#FFFFFF` | `#201C2D` | paneles, pentagrama |
| `--ink` | `#1B1726` | `#EEEBF6` | texto, forma de onda reproducida |
| `--ink-soft` | `#433D55` | `#C2BCD4` | texto secundario, forma de onda |
| `--ink-faint` | `#5D5770` | `#9B94B0` | rótulos, ejes (≥ 4,5:1 sobre `--paper`) |
| `--rule` | `#DCD8E6` | `#322C45` | líneas del pentagrama, separadores |
| `--edition` | `#3A2A8C` | `#2A2160` | banda de cabecera |
| `--on-edition` | `#F6F4FF` | `#F1EEFF` | texto sobre la banda |
| `--focus` | `#5B3FD9` | `#B7A6FF` | anillo de foco |
| `--alert` | `#A3261B` | `#FF9A8F` | rechazo, servicio caído |
| `--clear` | `#17683D` | `#7FD9A4` | sin coincidencias |

**Resaltadores** (`--ink-a` … `--ink-f`), en el orden en que aparecen las canciones:
amarillo `#FFDD33`, rosa `#FF8CC6`, verde `#78DE8A`, naranja `#FFB056`, celeste `#79CBFF`,
violeta `#C7A4FF`. Son claros a propósito: el texto encima siempre es `--ink` del tema claro
(contraste > 8:1 en los seis), también en oscuro. Van **debajo** de la forma de onda, como el
resaltador queda bajo la tinta impresa: la forma de onda se sigue leyendo encima, en los
dos temas.

**Nunca solo color**: cada canción lleva una letra (A, B, C…) en el trazo, en la lista y en
el reproductor. Una coincidencia **bajo el umbral** no es un trazo lleno: es un rayado del
mismo color con borde discontinuo, y su texto dice «débil».

## Tipografía

Una familia, pensada para leerse sin ambigüedad (también proyectada): **Atkinson
Hyperlegible Next** para todo el texto y **Atkinson Hyperlegible Mono** solo para medidas
(tiempos, desfases, porcentajes, milisegundos), con cifras tabulares. Auto-hospedadas
(`@fontsource-variable`), porque la CSP solo admite fuentes del mismo origen.

| Paso | Tamaño | Peso | Uso |
|---|---|---|---|
| `--text-xs` | 0,8125 rem | 500 | rótulos del eje, letras de canción |
| `--text-sm` | 0,9375 rem | 400 | metadatos, tablas |
| `--text-md` | 1 rem | 400 | cuerpo |
| `--text-lg` | 1,25 rem | 600 | títulos de panel |
| `--text-xl` | 1,75 rem (1,5 en móvil) | 650 | la conclusión |

Escala fija en rem, sin tamaños fluidos. Interlineado 1,5 en cuerpo, 1,2 en la conclusión.

## Espacio y forma

Base de 4 px: `--space-1` 4 · `--space-2` 8 · `--space-3` 12 · `--space-4` 16 ·
`--space-6` 24 · `--space-8` 32 · `--space-12` 48. Más aire sobre un título que debajo.
Radios cortos, de papel cortado: 4 px en fichas, 6 px en paneles. Sombras casi nulas: el
papel se separa por filete (`--rule`), no por elevación. Objetivos táctiles de 44 px.

## Movimiento

- Estado, no decoración: 160 ms con `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- **Un solo momento**: cuando llega el resultado, cada trazo de resaltador se pinta de
  izquierda a derecha (`clip-path`, 420 ms, escalonado 60 ms), como el gesto del marcador.
- El cabezal de reproducción se mueve con el audio (`requestAnimationFrame`): es estado.
- `prefers-reduced-motion: reduce`: sin transiciones ni el trazo animado; el cabezal sigue
  porque dice dónde suena.

## Componentes

- **Cabecera**: banda de edición con el nombre, «copyright en audio», el estado del
  servicio (punto y texto, nunca solo color) y el tema (sistema, claro, oscuro).
- **Zona de subida**: un pentagrama vacío del ancho de la línea de tiempo, con la invitación
  y un botón «Elegir archivo»; acepta arrastrar. Subiendo: barra de progreso con el
  porcentaje y «Cancelar». Analizando: el pentagrama con la forma de onda ya grabada y un
  barrido indeterminado sobre ella.
- **Conclusión**: una frase en lenguaje claro, con su marca (resaltador, verificación o
  alerta) y debajo los datos del archivo (formato, duración, ventanas).
- **Línea de tiempo** (el elemento memorable): pentagrama, forma de onda, barras de compás,
  regla de tiempos en mono, trazos por coincidencia en carriles si se solapan. Cada trazo es
  un botón: se elige con clic, Tab o las flechas.
- **Coincidencias**: una tabla con letra, canción y artista, tramo de la subida, tramo del
  original, desfase y puntaje; la fila elegida coincide con el trazo elegido.
- **Escucha con un clic**: la letra de un tramo es el botón que lo hace sonar (tu audio) y el
  tramo del original, el del original; la que suena lleva un anillo índigo y el símbolo de
  pausa. Las débiles se esconden tras «Mostrar coincidencias débiles (n)».
- **Tiempos por etapa**: una barra apilada fina y su tabla, en un desplegable callado.

## Lo que no se hace

Sin degradados, sin cristal, sin insignias ni anillos de progreso, sin emojis como iconos
(iconos SVG propios de trazo 1,75 px), sin rótulo sobre los títulos, sin tarjetas iguales en
rejilla, sin color de resaltador fuera de los datos.
