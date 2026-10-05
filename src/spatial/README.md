# Geometría y métricas espaciales

El único lugar donde viven los puntos, los rectángulos, los polígonos y las dos distancias
de la Parte 2. El R-Tree, el evaluador de expresiones y el recorrido secuencial usan
exactamente las mismas funciones, así que una consulta **no puede dar resultados distintos
según el camino que elija el planificador**.

No depende de ningún otro paquete del motor: es geometría pura.

## Puntos (`geometry.py`)

Un punto es `Point(lat, lon)`, en grados decimales y **en ese orden**, que es el del
enunciado: `POINT(-12.0464, -77.0428)` es la Plaza de Armas de Lima.

`geographic_point(lat, lon)` construye el punto comprobando los rangos: latitud en
`[-90, 90]` y longitud en `[-180, 180]`. Todo punto que llega al disco pasa por ahí, así
que el R-Tree y Haversine pueden dar por hecho que las coordenadas son válidas.

## Rectángulos: el MBR

`Rectangle(min_lat, min_lon, max_lat, max_lon)` es el rectángulo alineado a los ejes que el
R-Tree guarda por cada nodo. Lo que el árbol le pide:

| Operación | Para qué |
|---|---|
| `union`, `enclosing` | el MBR que cubre a varios |
| `area`, `area_with`, `enlargement` | elegir dónde insertar y cómo dividir un nodo |
| `intersects`, `contains_point` | decidir si una búsqueda entra en un nodo |

## Polígonos

`Polygon(vértices)` es un polígono simple; el último vértice se une con el primero.
`contains(punto)` usa la **regla par-impar**: se lanza un rayo desde el punto hacia
longitudes crecientes y se cuentan las aristas que cruza. Impar, dentro; par, fuera.

Dos detalles que los tests cubren:

- **El borde cuenta como dentro.** Se comprueba aparte, porque el rayo por sí solo lo
  decidiría de forma distinta según la arista sobre la que caiga el punto.
- **Un rayo que pasa justo por un vértice** cruza una sola de las dos aristas que se tocan
  en él: de cada arista cuenta su extremo de menor latitud y no el de mayor.

Coste `O(vértices)`. Funciona igual con polígonos cóncavos.

## Las dos métricas (`metrics.py`)

| | Euclidiana | Haversine |
|---|---|---|
| En SQL | `distancia(a, b, metrica='euclidiana')` | `distancia(a, b)` |
| Qué mide | línea recta sobre el plano de coordenadas | arco sobre la esfera terrestre |
| Unidad | grados | metros |
| Cuándo | coordenadas planas, o distancias muy cortas | cualquier distancia sobre el mapa |

**Haversine** entre `(φ₁, λ₁)` y `(φ₂, λ₂)`, con `R = 6 371 008.8 m`:

```
a = sin²(Δφ/2) + cos φ₁ · cos φ₂ · sin²(Δλ/2)
d = 2R · asin(√a)
```

El radio es el radio medio de la Tierra, el mismo que usa PostGIS al medir sobre la esfera:
entre la Plaza de Armas de Lima y la de Cusco las dos dan **573 007.08 m**.

**Por qué no basta la euclidiana:** un grado de longitud mide 111 km en el ecuador y la
mitad a 60° de latitud. Medir en grados deforma el mapa, y las dos métricas pueden incluso
discrepar sobre quién es el vecino más cercano (hay un test que lo demuestra).

### La distancia mínima a un rectángulo

Cada métrica da también `min_distance(punto, rectángulo)`: la menor distancia posible del
punto a cualquier punto del rectángulo, y 0 si está dentro. Es lo que permite al R-Tree
**descartar un nodo sin abrirlo**, y tiene que ser una cota inferior de verdad: si se pasara
de larga, el árbol podaría un nodo que sí tenía resultados.

- **Euclidiana:** la distancia al lado o a la esquina más cercanos.
- **Haversine:** si la longitud del punto cae dentro del rango del rectángulo, el punto más
  cercano está en su mismo meridiano y la distancia es la diferencia de latitudes. Si cae
  fuera, está sobre el borde este u oeste; a lo largo de ese meridiano la distancia tiene un
  único mínimo, en la latitud `atan2(sin φ, cos φ · cos Δλ)`, y basta compararlo con los dos
  extremos del borde. **No es la distancia a la esquina más cercana**: desde 60° N, el punto
  más próximo de un borde lejano queda entre sus dos extremos.

Una cota tiene que ser inferior **también después de redondear**. La distancia a un punto
y la cota de su rectángulo se calculan por caminos distintos, y con que la cota saliera
una cifra por encima el R-Tree perdía un resultado situado justo en el borde del radio.
Por eso a la de Haversine se le resta una fracción de `10⁻⁹` (`BOUND_SLACK`): cubre de
sobra el error de la fórmula —del orden de `10⁻¹⁵`, y hasta `10⁻¹⁰` cerca de las
antípodas— y no cambia qué nodos se abren.

## Tests

```bash
.venv/bin/python -m pytest tests/spatial -q
```

`min_distance` se comprueba contra fuerza bruta: para 300 rectángulos y puntos al azar por
todo el globo, se compara con el mínimo real sobre una malla de 1 681 puntos del
rectángulo. Tiene que ser **menor o igual** (para no perder resultados) y **casi igual**
(para que de verdad pode). Contra puntos que sí pertenecen al rectángulo —sus esquinas, y
el rectángulo de un solo punto— la comparación es **sin tolerancia**: ahí es donde un
redondeo hacia arriba se notaría.
