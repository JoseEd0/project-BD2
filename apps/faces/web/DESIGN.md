# Diseño: Rostros

La app es un **fotomatón de bolsillo**: la cabina de fotos carnet de una galería o de un
centro comercial, con su cortina azul petróleo, la luz que avisa «ahora», el destello y la
tira de fotos que sale por la ranura. De ese mundo toma cuatro cosas y nada más: la paleta, la
tipografía de rótulo, la densidad de una cabina (poco, grande, claro) y un gesto propio, **la
tira**. La navegación y los controles son los de cualquier app de cámara del celular.

## Escena

Una persona de pie en un aula o un pasillo, con el celular en una mano, bajo tubos
fluorescentes o a contraluz de una ventana. La pantalla de cámara es oscura por naturaleza
(el video manda y todo lo demás se apaga a su alrededor); las pantallas de texto
(consentimiento, mis datos) siguen el tema del sistema, claro u oscuro, o el que se elija
con los tres íconos (automático, claro, oscuro) de la barra de cada una.

## Color

Estrategia: **comprometida en el armazón, contenida en los estados**. La cortina petróleo es
el fondo de la cámara y la cabecera de las pantallas de texto; los colores de estado solo
aparecen donde significan algo.

| Token | Claro | Oscuro | Para qué |
|---|---|---|---|
| `--curtain` | `#0c2b3a` | `#0c2b3a` | la cortina: fondo del escenario de la cámara y de la cabecera, en los dos temas |
| `--curtain-raised` | `#13394b` | `#13394b` | paneles sobre la cortina (la tira, la hoja de instrucciones) |
| `--ground` | `#eef3f6` | `#081c26` | fondo de las pantallas de texto |
| `--surface` | `#ffffff` | `#0f2733` | bloques de texto, campos |
| `--ink` | `#0a1d27` | `#eef4f7` | texto |
| `--ink-soft` | `#3c5664` | `#a9bfcb` | texto secundario (≥ 4,5:1 sobre `--ground` y `--surface`) |
| `--line` | `#c3d2da` | `#24485a` | divisores y bordes de campos |
| `--flash` | `#f6fafc` | `#f6fafc` | el destello: acción principal sobre la cortina, toma aceptada |
| `--lamp` | `#ffc94a` | `#ffc94a` | la luz de «ahora»: la pose que toca, la instrucción vigente, el foco |
| `--known` | `#63e2c6` | `#63e2c6` | rostro reconocido (siempre con texto `--curtain` encima) |
| `--unknown` | `#ff9a6b` | `#ff9a6b` | «desconocido» (ídem) |
| `--danger` | `#b3261e` | `#ff8a80` | borrar, errores |
| `--focus` | `--lamp` sobre la cortina, `#0b5c80` sobre `--ground` claro | | anillo de foco de 3 px |

Sobre el video nunca hay texto suelto: cada etiqueta va en una placa sólida (`--known`,
`--unknown` o `--curtain` al 88 %) con texto oscuro o claro que pasa 4,5:1 sin importar lo
que muestre la cámara. «Desconocido» se distingue también por la forma (caja con trazo
discontinuo), no solo por el color.

## Tipografía

Una sola familia, la del sistema (`system-ui`, San Francisco en iPhone, Roboto en Android):
carga instantánea, la que el usuario ya lee bien a un brazo de distancia. Cifras tabulares
(`font-variant-numeric: tabular-nums`) en la latencia y en el código. El código de la persona
va en monoespaciada porque es un dato que se copia carácter por carácter.

| Paso | Tamaño | Uso |
|---|---|---|
| `--text-xs` | 0,8125 rem | marcas de la tira, unidades |
| `--text-sm` | 0,9375 rem | texto secundario, placas sobre el video |
| `--text-md` | 1,0625 rem | cuerpo, botones |
| `--text-lg` | 1,3125 rem | instrucción vigente sobre la cámara |
| `--text-xl` | 1,75 rem | títulos de pantalla |

Peso 600 en títulos y botones, 700 en la instrucción vigente; interlineado 1,45 en el cuerpo.

## Espacio y forma

Ritmo de 4 px (`--space-1` = 4 px … `--space-8` = 48 px). Radio de 14 px en paneles y en los
cuadros de la tira, 999 px en los botones (píldoras, como los de una cabina). Todo objetivo
táctil mide al menos 48 px de alto. Los márgenes respetan `env(safe-area-inset-*)`.

## Composición de la cámara

```
┌──────────────────────────────┐  cabecera sobre el video: volver · título · estado (latencia, red)
│ ‹  Reconocer        ● 38 ms  │
│                              │
│         ╭────────╮           │  óvalo de encuadre (registro) o cajas con su placa (reconocer)
│         │        │           │
│         ╰────────╯           │
│                              │
├──────────────────────────────┤  hoja inferior (cortina): instrucción vigente en grande,
│  Gira un poco la cabeza →    │  la tira de tres tomas y los controles al alcance del pulgar
│  [ Frente ✓ ][ Izq. ][ Der. ]│
│  ( ⟲ cámara )   ( Pausar )   │
└──────────────────────────────┘
```

- El video ocupa toda la pantalla (`100dvh`, `object-fit: cover`); la cámara frontal se
  muestra espejada, como un espejo, y las cajas se espejan con ella.
- Todo lo que se toca está en el tercio inferior. Arriba solo hay lectura (estado) y volver.

## El gesto propio: la tira

La tira de la cabina es el progreso del registro: tres cuadros del **mismo tamaño sobre la
misma línea base** (Frente, Izquierda, Derecha). El que toca lleva el borde `--lamp` (la luz
de «ahora»); uno aceptado se llena de `--flash` con su marca y su nombre, y el visor entero
destella una vez (un velo blanco de 180 ms; con movimiento reducido, sin destello). No guarda
fotos: el cuadro muestra el dibujo de la pose, no la cara, igual que el servicio no guarda la
foto. En el reconocimiento, cada caja lleva su **placa** debajo, como el pie impreso de la
tira: nombre y parecido, o «desconocido».

## Movimiento

150 a 220 ms, salida exponencial (`cubic-bezier(0.16, 1, 0.3, 1)`). Solo cambia lo que cambia
de estado: la caja sigue al rostro (transición de 120 ms de posición), el destello de una
toma aceptada, la hoja que cambia de instrucción. Con `prefers-reduced-motion: reduce`, todo
salta sin animarse.

## Componentes

- **Botón principal**: píldora `--flash` con texto `--curtain` sobre la cortina; petróleo
  con texto blanco sobre `--ground` claro. **Secundario**: contorno de 2 px. **Peligro**:
  contorno `--danger` aislado con espacio de sus vecinos; el borrado pide confirmar en línea.
- **Inicio**: la acción principal («Registrarme») es un bloque petróleo con la luz `--lamp`
  como flecha; las demás van en **una sola lista** con divisores, cada fila con su ícono, su
  nombre y para qué sirve en una línea. Nada de tarjetas iguales apiladas.
- **Tema**: tres íconos (automático, claro, oscuro) en una píldora `--curtain-raised` en la
  barra de cada pantalla de texto; el elegido, en `--flash`.
- **Aceptar algo** (el consentimiento): la casilla y el botón van juntos en la barra fija
  de abajo, sobre fondo sólido, para que el botón nunca quede sin su condición a la vista.
- **Estado**: una píldora con punto (verde conectado, ámbar «red lenta», gris «en pausa»,
  rojo «sin conexión») y la latencia en cifras tabulares.
- **Avisos de problema** (sin cámara, sin permiso, sin HTTPS, servicio caído): una pantalla
  con un título que dice qué pasa, los pasos para salir en una lista numerada y una sola
  acción principal.
- **Íconos**: SVG propios de trazo 2 px con puntas redondeadas, 24 px; nunca emoji.

## Lo que no es

Ni la estética de vigilancia (verde fósforo sobre negro, retículas, «escaneando…»), ni un
panel de administración, ni crema con serifa. La cámara se siente como una cabina amable:
oscura, cálida en la luz que indica, limpia en todo lo demás.
