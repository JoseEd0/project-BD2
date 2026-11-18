# Fotos de prueba

Tres fotos de la NASA, de **dominio público** (las obras de empleados del gobierno de
Estados Unidos no tienen derechos de autor; ver las [normas de uso de la NASA](https://www.nasa.gov/nasa-brand-center/images-and-media/)).
Se bajaron de Wikimedia Commons, se redujeron con OpenCV (`INTER_AREA`) y se guardaron en
JPEG de calidad 85, sin metadatos.

| Archivo | Tamaño | Qué muestra | Origen |
|---|---|---|---|
| `collins-1998.jpg` | 360 x 450 | Eileen M. Collins, comandante del STS-93, 1998 (foto de Robert Markowitz, NASA, GPN-2000-001177) | [Commons](https://commons.wikimedia.org/wiki/File:Commander_Eileen_Collins_-_GPN-2000-001177.jpg) |
| `collins-early.jpg` | 360 x 456 | Eileen M. Collins, retrato de sus primeros años en la NASA | [Commons](https://commons.wikimedia.org/wiki/File:Eileen_Collins,_early_NASA_portrait.jpg) |
| `sts-114-crew.jpg` | 720 x 574 | los siete tripulantes del STS-114, con Collins adelante a la derecha (NASA/JSC, sts114-s-002) | [Commons](https://commons.wikimedia.org/wiki/File:STS-114_crew.jpg) |

Con ellas los tests cubren lo que ninguna imagen sintética da: una misma persona en dos
fotos distintas (y una tercera, en grupo), siete personas distintas en una foto y un rostro
real que YuNet tiene que encontrar. Medido con SFace: Collins contra Collins da un coseno
de 0,74 a 0,83; Collins contra cualquier otro tripulante, menos de 0,18.
