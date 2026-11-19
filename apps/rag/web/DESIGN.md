# Design

Papers es una herramienta de lectura e investigación (ver [`PRODUCT.md`](PRODUCT.md)): una
pantalla de trabajo donde se pregunta, se lee la respuesta citada y se comprueba cada cita en
la página del PDF. Modo **Operate** para el armazón, con tipografía de **Read** para la
respuesta y los pasajes.

## El mundo: una microficha de diazo

Antes de la web, el paper que no estaba en el estante se leía en microficha: una hoja de
película con las páginas en una grilla de cuadros, una franja de título legible a simple
vista y un lector que proyecta un cuadro a la vez. El diazo la copiaba en índigo. De ahí salen
cuatro cosas, y solo cuatro (el armazón, la navegación y los controles son los estándar de la
web):

| Aporta | Cómo |
|---|---|
| Paleta | acetato frío y tinta de hierro, un solo índigo de diazo para todo lo que es cita, selección o acción; en oscuro, la película en negativo |
| Tipo | la franja de título en Archivo condensado y en mayúsculas; las respuestas y los pasajes en Source Serif 4 con tamaño óptico |
| Densidad | reglas de 1 px, radios de 4 px, tablas con columnas fijas |
| Gesto propio | **la ficha del paper**: sus páginas como cuadros en filas A, B, C…; las citadas, llenas de índigo; la abierta, recuadrada; las del fragmento, teñidas. Cada `[n]` enciende su cuadro y abre la página |

Se descartaron a propósito el chat genérico (barra de conversaciones, burbujas) y la sala de
lectura editorial (crema, serif de titular, terracota): los dos son lo que cualquier app de
esta categoría ya parece.

## Tokens

Definidos una vez por tema en `src/styles.css` (`:root` y `:root[data-theme="dark"]`, que el
kit fija antes de pintar). Los componentes solo los leen.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--ground` | `#e9ecf3` | `#0b0d19` | fondo de la mesa de trabajo |
| `--surface` | `#f7f8fb` | `#121527` | conversación, pestañas |
| `--surface-raised` | `#ffffff` | `#181c33` | campos, tablas, avisos |
| `--surface-sunk` | `#dfe3ec` | `#090a14` | la ficha, grupos de opciones |
| `--ink` / `--ink-2` / `--ink-3` | `#161a27` / `#414862` / `#575f7c` | `#e3e6f7` / `#b2b8d6` / `#959cc0` | texto, secundario, terciario |
| `--rule` / `--rule-strong` | `#c4cad9` / `#7d859c` | `#2a2f4d` / `#626a9a` | reglas; borde de controles (≥ 3:1) |
| `--diazo` | `#3633a3` | `#aab0ff` | citas, selección, acción primaria |
| `--diazo-soft` / `--diazo-wash` | `#e2e2f6` / índigo al 20 % | `#23274d` / al 32 % | fondo de una llave; el pasaje marcado sobre el PDF |
| `--strip` | `#26236f` | `#161939` | la franja de título |
| avisos | `--warn-*`, `--danger-*` | | incompleta, citas inválidas; fallos |

Contraste medido (WCAG): `--ink-3` sobre cualquier superficie ≥ 4,9:1 en claro y ≥ 6,2:1 en
oscuro; `--diazo` sobre `--diazo-soft` 7,6:1 / 7,1:1; texto de la franja ≥ 7,7:1; los avisos
≥ 6,5:1; `--rule-strong` ≥ 3,2:1 para los bordes de campos, selects y chips.

**Tipo.** `--font-ui` Archivo Variable (eje de ancho: 72 % en la marca, 80-85 % en etiquetas
de la franja y del SQL), `--font-read` Source Serif 4 Variable, `--font-mono` la del sistema
para SQL, cifras y llaves (`tabular-nums`). Escala fija en rem: 12, 13, 15, 17, 18 (lectura),
22, 28. La respuesta se lee a 18 px con interlineado 1,62 en una medida de 42 rem.

**Espacio.** 4, 8, 12, 16, 24, 32, 48 px (`--space-1` a `--space-7`).

**Movimiento.** 180 ms con `cubic-bezier(0.16, 1, 0.3, 1)` para cambios de estado; la hoja
del celular sube en 220 ms. `prefers-reduced-motion` apaga transiciones, el cursor que
parpadea y el desplazamiento suave hasta el pasaje.

## Composición

```
┌ PAPERS · 1 495 documentos · cs.DB · cs.IR · curso ───────── ● En línea  Auto|Claro|Oscuro ┐
├──────────────── conversación (44 %) ─────────┬──────── mesa de trabajo (56 %) ─────────────┤
│ pregunta (h2, sans)                           │ Página | Recuperación | Tiempos y costo      │
│ respuesta en serif con llaves [1] [2]         │ título del paper · autores · licencia        │
│ avisos (incompleta, citas inválidas, fallos)  │ pasaje citado                                │
│ fragmentos enviados al modelo (1…8)           │ ‹ Página 2 de 14 ›   ficha A 1 2 3 … / B …   │
│ modelo · primer texto · total · tokens · USD  │ la página dibujada con pdf.js, pasaje marcado│
├───────────────────────────────────────────────┤                                              │
│ categorías · desde/hasta │ pregunta │ Preguntar│                                              │
└───────────────────────────────────────────────┴──────────────────────────────────────────────┘
```

- Debajo de 64 rem la mesa de trabajo no cabe: se abre como una hoja (`<dialog>` modal) desde
  abajo al tocar una cita o «Recuperación», y se cierra con su botón o con Escape.
- Debajo de 40 rem la franja deja solo la marca y el tema, y el botón de preguntar queda en su
  ícono (el texto sigue para los lectores de pantalla).

## Componentes

- **Llave de cita** (`.key`): un botón con el número, `aria-label` con el paper y la página,
  `aria-pressed` si su fragmento está abierto; blanco de toque ampliado con un pseudoelemento.
  Una cita inválida no es botón: borde discontinuo ámbar, tachada y explicada en un aviso.
- **Aviso** (`.notice`): título que dice qué pasó y texto que dice qué hacer; `role="alert"`
  solo para fallos. Acciones discretas (reintentar, ver los fragmentos).
- **Ficha** (`FicheGrid`): 10 cuadros por fila con la letra de la fila; cada cuadro es un
  botón con su estado en el nombre accesible («Página 2, citada, del fragmento»).
- **Tabla de recuperación**: columnas fijas `#`, BM25, Coseno, RRF y Fragmento en los tres
  métodos; los fragmentos que leyó el modelo, teñidos y con su llave.
- **SQL**: cada consulta con su etapa, el SQL con `$n` y los parámetros; una lista larga (los
  papers de un filtro) se resume.
- **Barras de etapa**: el `Server-Timing` como barras proporcionales al total, con el tiempo
  sin atribuir aparte.

## Estados

Vacío (preguntas de ejemplo), buscando (esqueleto), escribiendo (cursor), sin fragmentos (y
qué filtro quitar), los documentos no responden, incompleta por el tope, rechazada, citas
inválidas, respaldo a otro modelo, detenida, y cada fallo de `FAILURE_COPY`: sin clave del
modelo, tope diario, límite por segundo, modelo caído, configuración, gestor caído, red y
conexión cortada. El lector: abriendo la página, PDF que no llega, documento de texto (el
material del curso, sin PDF) y pasaje que no se pudo ubicar en el dibujo.

## El PDF

pdf.js (`pdfjs-dist`), cargado solo al abrir la primera cita. Dibuja la página a lo ancho del
panel con su capa de texto, pide el archivo por rangos (`Range`) y marca el pasaje comparando
el texto del corpus con el de la página reducidos a letras y dígitos (`reader/passage.ts`).
No se usa el visor del navegador en un `<iframe>`: la CSP del nginx de la app prohíbe
enmarcar el API (`frame-ancestors 'none'`, `object-src 'none'`), Safari en iOS no respeta
`#page=n` dentro de un `iframe`, y no habría forma de marcar el pasaje. «Negativo» invierte
la página para leer de noche; «Abrir el archivo» lleva al PDF entero en `#page=n`.
