# Producto: Rostros

## Plataforma

web (PWA instalable, pensada para el celular)

## Usuarios

Un compañero de clase o un visitante de la exposición del curso Base de Datos 2 que recibe un
enlace o un código QR, lo abre en **su propio celular** y quiere, en menos de un minuto,
registrarse con su cara y ver que la app lo reconoce. Lo usa de pie, con una mano, en un aula o
un pasillo con luz cambiante (tubos fluorescentes, ventana a contraluz), a veces con otras
personas al fondo. No instaló nada antes y no leerá instrucciones largas.

Un segundo usuario es quien expone: apunta la cámara trasera a otra persona o a un grupo para
mostrar el reconocimiento en vivo, con las cajas y la latencia a la vista.

## Propósito

Demostrar, en el celular de cualquiera, el reconocimiento facial del servicio `faces`
construido sobre el minigestor del curso: consentimiento informado, registro guiado en tres
tomas (de frente, girado a la izquierda, a la derecha) con controles de calidad en vivo, y
reconocimiento en tiempo real por WebSocket con nombre, confianza y latencia. El éxito es que
alguien que nunca vio la app termine el registro sin ayuda y se vea reconocido al instante, y
que pueda borrar sus datos cuando quiera desde la misma app.

## Posicionamiento

No es una app de vigilancia ni un álbum de fotos: es una demostración honesta de un dato
biométrico tratado con cuidado. **No guarda fotos** (solo una huella numérica por toma), pide
consentimiento con su versión, dice qué se guarda y cuánto tiempo, y entrega un código que es
la única llave para borrar el registro. Esa transparencia es parte del producto, no letra
chica.

## Contexto de uso

- Se abre por HTTPS: el certificado local del compose (`:8404`) o el túnel de Cloudflare
  (perfil `tunnel`). Sin HTTPS el navegador no da la cámara.
- Celulares reales: iPhone (Safari) y Android (Chrome), cámara frontal para registrarse y
  trasera para reconocer a otros. Red Wi-Fi del aula o datos móviles, a veces lenta.
- El API (`faces-api`) puede estar en varias réplicas detrás del nginx; una réplica llena
  cierra el WebSocket con `1013`, una conexión inactiva con `4408`.

## Capacidades y restricciones

- Consentimiento (`GET /consent`), registro (`POST /enrollments`, tomas, `finish`),
  reconocimiento en vivo (`WS /ws/recognize`), ver y borrar (`GET`/`DELETE /person` con
  `X-Person-Id`).
- Los mensajes de calidad de cada toma vienen del API, en español; la app no los reescribe.
- El código de la persona se muestra una sola vez y se guarda solo en este dispositivo.
- Sin cuentas, sin inicio de sesión, sin analítica.

## Voz

Español neutro, de tú, frases cortas y concretas: qué pasa y qué hacer ahora. Nada de jerga
técnica en la cara del usuario (sin «WebSocket», «409» ni «embedding»); la latencia sí se
muestra, en milisegundos, porque es parte de la demostración.

## Evidencia disponible

Solo las tres fotos de dominio público de la NASA de `apps/faces/tests/fixtures/` para pruebas.
No hay fotos de personas reales en la interfaz ni en sus capturas, y no se inventan.

## Principios

1. La cámara manda: la pantalla es el visor; todo lo demás se acomoda alrededor sin taparlo.
2. Una instrucción a la vez, siempre visible, que diga qué corregir.
3. Cada fallo dice qué pasó y cómo salir (permiso negado, sin HTTPS, red lenta, servicio
   caído), sin callejones sin salida.
4. La privacidad se ve: qué se guarda, el código para borrar y el botón para hacerlo.

## Accesibilidad

WCAG 2.1 AA: contraste sobre el video, objetivos táctiles de al menos 44 px, controles al
alcance del pulgar, foco visible y teclado en el escritorio, movimiento reducido respetado,
anuncios de estado para lectores de pantalla (`aria-live`) con la instrucción vigente.
