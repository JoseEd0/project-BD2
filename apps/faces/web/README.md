# Rostros: la PWA de la cámara

La interfaz propia del servicio de rostros ([`apps/faces`](../README.md)): una app para el
celular, instalable, en la que cualquiera **se registra con su cara en tres tomas y se ve
reconocido al instante**, y borra sus datos cuando quiera. Qué es y para quién está en
[`PRODUCT.md`](PRODUCT.md); cómo se ve —un fotomatón de bolsillo: cortina petróleo, la luz
ámbar de «ahora», el destello y la tira de tres tomas— en [`DESIGN.md`](DESIGN.md).

Está hecha sobre el [kit web](../../web-kit/README.md) (cliente tipado, salud, tema, PWA) y
habla solo con su API por `/api`, que el nginx de su imagen reenvía a `faces-api`.

| Pantalla | Ruta | Qué hace |
|---|---|---|
| Inicio | `/` | registrarse, reconocer, mis datos; el estado del servicio y el tema |
| Registro | `/registro` | el consentimiento servido por `GET /consent` (texto y versión, «sí» explícito), el nombre, la cámara con el óvalo y las tres tomas, el código final |
| Reconocer | `/reconocer` | la cámara (trasera por omisión) con una caja por rostro: nombre y parecido, «desconocido» en otro color y trazo, «acércate»; la latencia |
| Mis datos | `/mis-datos` | lo que se guarda de cada persona registrada desde este celular, y borrarla (`DELETE /person` con `X-Person-Id`) tras confirmar |

```
src/
  api/          el cliente tipado del contrato (openapi/faces.json → generated/faces.ts) y sus errores
  realtime/     el protocolo del socket, el planificador de cuadros, la reconexión y su hook
  camera/       pedir y soltar la cámara, sus problemas por navegador, capturar JPEG, la geometría
  enrollment/   la máquina de estados del registro y la compuerta que decide cuándo disparar
  storage/      los códigos guardados en este celular
  components/   el escenario de la cámara, las cajas, el óvalo, la tira, la píldora de estado
  pages/        una pantalla por ruta
```

## Reconocimiento en vivo: el cliente del socket

`WS /api/ws/recognize` con el protocolo del servicio: cada cuadro es una cabecera de 5 bytes
(versión `1` y número de cuadro, `uint32` big-endian) y un JPEG de 640 px de lado mayor al 70 %
(unos 40 KB), sacado del video con un `canvas` **sin espejar** aunque la vista previa de la
cámara frontal sí lo esté.

- **Contrapresión sin cola** ([`scheduler.ts`](src/realtime/scheduler.ts)). Sale un cuadro
  solo si hay menos de `maxInFlight` sin respuesta (2 al reconocer, 1 durante el registro),
  si pasó el intervalo que pide el servidor en `ready.limits.max_fps` y si el socket ya envió
  lo anterior (`bufferedAmount` en 0). Nunca se acumulan cuadros: el que se captura es siempre
  el de ahora. Como el servidor descarta los que esperaban, una respuesta libera también los
  cuadros anteriores a ella; uno sin respuesta en 4 s se da por perdido.
- **Latencia y red lenta.** El número del cuadro vuelve en su `result`, así que el tiempo de
  ida y vuelta se mide sin relojes compartidos; se suaviza con una media móvil y la píldora
  dice «Red lenta» si pasa de 700 ms o si un cuadro lleva más que eso sin respuesta. Debajo
  se ven los tiempos del servidor (`timings`: total, detectar, embeber y buscar).
- **Reconexión** ([`reconnect.ts`](src/realtime/reconnect.ts)). `1013` (réplica llena):
  reintenta con espera exponencial desde 2 s hasta 30 s y avisa que hay mucha gente; `1011` o
  un corte: desde 0,5 s hasta 15 s, y desde el segundo intento explica si el servicio no
  responde (`/health`); `4408` (inactiva): queda «En pausa · toca para seguir» y vuelve con el
  siguiente toque o tecla; `1000`: la cerró la app. Al reconectarse manda `hello` con la
  `session` del último `ready`, así que sigue con los mismos rastros en cualquier réplica.
- **Pestaña oculta.** Con `visibilitychange` se detienen las pistas de la cámara (se apaga su
  luz) y se cierra el socket; al volver se piden de nuevo.

## Registro guiado

`consent → name → starting → capture → finishing → done | blocked`
([`machine.ts`](src/enrollment/machine.ts), un reducer puro). Durante las tomas el socket corre
en paralelo solo para saber **dónde está la cara**, y una compuerta
([`precheck.ts`](src/enrollment/precheck.ts)) dispara la toma (JPEG de 1 280 px al 90 %,
`POST …/shots`) cuando la cara está centrada en el óvalo y con buen tamaño en dos respuestas
seguidas; una a la vez, a no menos de 1,2 s entre tomas (el nginx admite 5 peticiones por
segundo por IP) y 2,2 s después de un rechazo, para leer el motivo. Si el socket no responde,
dispara a ciegas cada 3 s y deja que juzgue el servidor; «Tomar ahora» dispara a mano.

El mensaje de cada rechazo es el del API, tal cual; la pose que falta se dice con el mensaje
del servidor y una flecha hacia el lado correcto **de la pantalla** (la izquierda de la
persona queda a la izquierda en el espejo de la frontal y a la derecha con la trasera). Un
`409` de duplicado o de parecido termina en una pantalla que lo dice; una sesión vencida o
sin intentos, en «Empezar de nuevo». Si la persona se va a mitad, la sesión se cancela
(`DELETE /enrollments/{id}`, también al cerrar la pestaña con `keepalive`).

Al terminar, el código de la persona se muestra **una sola vez**, en grupos de cuatro, con
«Copiar código», y se guarda en este celular (`localStorage`) para «Mis datos». Si el
navegador no deja guardarlo (navegación privada), la pantalla lo dice.

## Casos borde

| Caso | Qué ve la persona |
|---|---|
| Sin HTTPS (`navigator.mediaDevices` no existe) | «Abre la app por HTTPS», con la dirección actual, el túnel y, desde `:8104`, un botón a la misma página en `:8404` |
| Permiso negado | los pasos para permitir la cámara en iPhone, Chrome de Android, Samsung Internet, Firefox o escritorio, e «Intentar de nuevo» |
| Sin cámara / cámara ocupada / navegador dentro de otra app | qué pasó y cómo salir |
| Servicio caído | en el inicio, «Servicio no disponible» con el motivo; sobre la cámara, «El servicio de rostros no responde» y «Reintentar ahora» |
| Réplica llena, conexión inactiva, red lenta | ver *Reconocimiento en vivo* |
| Versión nueva desplegada | un aviso «Hay una versión nueva» que se aplica cuando la persona lo pide |

## Comandos

```bash
npm ci
npm run dev        # http://localhost:5173, /api (y el socket) reenviados a WEB_DEV_API_URL
npm run build      # tipos (tsc -b) y bundle con el service worker
npm test           # Vitest: protocolo, planificador, reconexión, geometría, compuerta, estados, pantallas
npm run e2e        # Playwright con la cámara simulada de Chromium (después de build)
npm run contracts  # vuelve a exportar openapi/faces.json y a generar src/api/generated/faces.ts
```

Los tests de Playwright usan la cámara simulada de Chromium
(`--use-fake-device-for-media-stream`) en un Pixel 7 emulado, el API simulado con
`context.route` y el socket con `page.routeWebSocket`, que responde a cada cuadro con cajas en
los píxeles del JPEG recibido. Cubren registrarse con un rechazo y tres tomas automáticas,
reconocerse y borrar los datos; el duplicado; la guía que pide acercarse sin gastar tomas; la
réplica llena; el cierre por inactividad con su `hello`; la pestaña oculta; el permiso negado;
sin HTTPS; sin cámara; el servicio caído, y la PWA (manifiesto, sin conexión y tema).

## Probar en un celular de verdad

Los tests no reemplazan un iPhone y un Android. Con el compose arriba y el túnel
(`docker compose --profile tunnel up -d faces-tunnel`, la dirección `…trycloudflare.com` en
`docker compose logs faces-tunnel`) o por `https://<ip-del-equipo>:8404` aceptando el
certificado:

1. Abrir la dirección en **Safari (iPhone)** y en **Chrome (Android)**; el inicio dice
   «Servicio disponible».
2. Instalar: Compartir › «Agregar a inicio» (iPhone) o el aviso «Instalar app» (Android);
   abrirla desde el ícono, sin barra del navegador.
3. Registrarse: el consentimiento no deja seguir sin marcar la casilla; el permiso de cámara
   se pide una vez; la vista previa de la frontal está espejada.
4. Con poca luz o tapando media cara, el aviso dice qué corregir y no se gastan tomas; al
   girar a la izquierda la flecha apunta al lado correcto; las tres tomas se llenan en la
   tira y el visor destella con cada una.
5. Aparece «Listo» con el código; «Copiar código» funciona; «Probar ahora» muestra la caja
   verde con el nombre y la latencia; otra persona sale en naranja como «desconocido».
6. Cambiar a la cámara trasera y apuntar a alguien registrado: la caja ya no va espejada y
   sigue a la cara.
7. Bloquear el celular o pasar a otra app 10 s: la luz de la cámara se apaga; al volver,
   sigue sola.
8. Activar el modo avión 5 s: «Sin conexión · reintento…»; al volver la red, «En vivo».
9. Negar el permiso de la cámara (Ajustes del sitio) y entrar a «Reconocer»: los pasos son
   los de ese navegador; tras permitir, «Intentar de nuevo» funciona.
10. Abrir por `http://<ip>:8104` (sin HTTPS): explica cómo entrar y ofrece la dirección segura.
11. «Mis datos» › «Borrar mis datos» › «Sí, borrar»: lo confirma, y «Reconocer» ya no muestra
    el nombre (a lo sumo en 2 s, lo que dura la identidad de un rastro).
12. Ajustes del sistema en tema oscuro y texto grande: todo se lee y nada se corta.
