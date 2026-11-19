# Product
<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Quien tiene que decidir si un audio usa música ajena antes de publicarlo o de aceptarlo:
el editor de un canal o de un podcast que revisa una pista, quien modera las subidas de una
plataforma o el productor que quiere saber si su mezcla deja ver los samples que usó. Llega
con un archivo concreto en la mano, lo sube y necesita una respuesta que pueda defender: qué
canción, en qué tramo, con qué desfase y cuánto se parece. Escucha antes de decidir.

También lo usa el alumno del curso para mostrar la detección en la exposición: sube un
remix del catálogo de muestra y señala los tramos.

## Product Purpose

Detectar si una canción contiene fragmentos de canciones del catálogo (las 7 994 pistas de
`fma_small` del Free Music Archive) y mostrar **dónde**: la línea de tiempo del audio subido
con cada tramo coincidente, su canción original, su puntaje y su desfase, y la escucha A/B
de la subida y del original desde el mismo instante. El éxito es que la persona confirme o
descarte una copia con los oídos, en menos de un minuto, sin leer JSON.

## Positioning

No es un «sí/no»: la detección del servicio alinea ventanas de 5 s por votación por desfase,
así que cada coincidencia trae el tramo exacto de la subida, el tramo del original y el
desfase entre los dos. La interfaz convierte eso en evidencia que se escucha: los dos audios
desde el mismo punto, alternando con una tecla.

## Operating Context

- Un archivo de audio (MP3, WAV, FLAC u OGG, hasta 48 MiB y 10 min) arrastrado o elegido.
- `POST /api/detections` responde en medio segundo para 3 min de audio; las etapas llegan en
  `Server-Timing` (`probe`, `loudness`, `segments`, `decode`, `extraction`, `search`,
  `voting`, `songs`).
- El original se escucha por `GET /api/songs/{id}/audio`, con `Range`.
- Se usa en un escritorio con auriculares, y a veces en un teléfono para revisar algo rápido.
- La exposición del curso se proyecta: tiene que leerse a distancia.

## Capabilities and Constraints

- El umbral de copia es 0,43, calibrado con la curva ROC de #77; la respuesta trae
  `threshold`, `score`, `coverage`, `mean_similarity` y `above_threshold` por coincidencia. Una coincidencia bajo
  el umbral se muestra como débil, no como copia.
- El audio subido no sale del navegador más que hacia el API de la app; la forma de onda se
  calcula en el navegador.
- Errores con motivo: `415` formato no soportado, `400` vacío, dañado o de menos de 1 s,
  `413` demasiado pesado o largo; servicio caído según `/health`.
- Interfaz en español.

## Brand Commitments

- Nombre del producto: **Songcheck** (el paquete del servicio), con el subtítulo «copyright
  en audio».
- Una app aparte, con identidad propia: nada visual de las otras apps de la Parte 5 ni de la
  consola del gestor.

## Evidence on Hand

- El catálogo real de FMA y el de muestra (`songcheck.sample`, cuatro melodías generadas)
  para la demo y los tests.
- Las cifras medidas de la detección (`apps/copyright/README.md`, §14 de
  `benchmarks/README.md`). No se inventan clientes, precisiones ni casos.

## Product Principles

1. La evidencia se escucha: toda coincidencia se puede oír contra su original en un gesto.
2. Decir dónde, no solo si: tramo, desfase y puntaje siempre visibles.
3. Honestidad del umbral: lo que no pasa del umbral se muestra como débil, nunca como copia.
4. Cada estado tiene una salida: rechazo con su motivo y qué hacer, servicio caído con
   reintento.

## Accessibility & Inclusion

WCAG 2.2 AA: contraste, todo usable con teclado (la escucha A/B incluida), foco visible,
movimiento reducido respetado, 390 px de ancho sin desplazamiento horizontal. La canción de
cada tramo no se distingue solo por color: lleva su nombre y su número.
