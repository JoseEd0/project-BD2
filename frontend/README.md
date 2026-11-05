# Frontend

Interfaz tipo IDE de base de datos, en React + TypeScript + Vite, con **una página por parte
del proyecto** dentro de la misma aplicación:

| Página | Dirección | Qué tiene |
|---|---|---|
| **Consola SQL** | `#/` | las Partes 1 y 2: archivos, editor, resultados, plan, mapa y esquema |
| **Texto** | `#/texto` | la Parte 3: TF-IDF y BM25 lado a lado sobre el índice invertido |

Las dos comparten el cliente del API, **la sesión** —y con ella la transacción abierta—, el
catálogo de tablas, el tema y el build. En la consola, los cuatro paneles que pide el
enunciado están **visibles a la vez**, para que en una sola pantalla se vea la consulta, sus
filas y cómo se ejecutó.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Gestor  Consola SQL · Texto  [Consulta][Esquema]  ● Autocommit  [⟳] │
├──────────────╥───────────────────────────────────────────────────────┤
│ ARCHIVOS     ║ CONSULTAS         1 sentencia  [Historial] [Ejecutar] │
│  ⌕ filtro    ║  1  SELECT ciudad, COUNT(*) AS total                  │
│  tablas      ║  2  FROM alumnos GROUP BY ciudad;                     │
│  columnas    ║  atajos de ejemplo                                    │
│  índices     ╠═══════════════════════════╤═══════════════════════════┤
│              ║ RESULTADOS  [Filas 3]     │ PLAN DE EJECUCIÓN         │
│              ║ [Mensajes]  [Copiar][CSV] │  árbol de operadores      │
├──────────────╨───────────────────────────┴───────────────────────────┤
│ ● Motor conectado · Tablas 3 · Filas almacenadas 9 · Tiempo 2.17 ms  │
└──────────────────────────────────────────────────────────────────────┘
   ║ y ═ son divisores arrastrables
```

## Navegación entre páginas

La cabecera lleva los enlaces a las páginas. El router es **la parte de la URL tras `#`**,
hecho a mano en [`src/routes.ts`](src/routes.ts) y
[`src/hooks/useHashRoute.ts`](src/hooks/useHashRoute.ts) (`useSyncExternalStore` sobre el
evento `hashchange`), sin dependencias: no hace falta configurar el servidor, y «atrás» y
«adelante» del navegador funcionan solos.

- **Cada página es un trozo aparte del bundle** (`React.lazy` + `Suspense`): se descarga la
  primera vez que se abre. Mientras llega, se ven la cabecera y la barra de estado de
  siempre.
- **Una página abierta sigue montada, oculta**, al pasar a otra: la consola conserva su
  editor, su resultado y su mapa, y la página de texto su búsqueda.
- Lo compartido vive en [`useWorkspace`](src/hooks/useWorkspace.ts): la sesión, la
  transacción, las tablas, la conexión, el tema y la regla de **una petición a la vez**. Una
  búsqueda de la página «Texto» corre dentro del `BEGIN` que se abrió en la consola.

| Bundle (`npm run build`) | Antes | Ahora |
|---|---:|---:|
| JS inicial (`index-*.js`) | 363,0 kB (112,2 kB gzip) | 151,1 kB (49,2 kB gzip) |
| CSS inicial (`index-*.css`) | 41,0 kB (12,0 kB gzip) | 31,0 kB (6,6 kB gzip) |
| consola (`ConsolePage-*`), perezosa | — | 212,5 kB JS + 15,6 kB CSS (Leaflet) |
| texto (`TextSearchPage-*`), perezosa | — | 22,2 kB JS |
| compartido por las dos (`InvertedIndexView-*`) | — | 9,0 kB JS |

## Las dos vistas de la consola

### Consulta

- **Archivos** — las tablas y su estructura: organización física (heap file, secuencial o
  B+ agrupado), número de filas, columnas con su tipo, marca de clave primaria, `NOT NULL`
  y **con qué índice está indexada cada columna**. Tiene filtro por nombre de tabla o de
  columna, y cuatro botones por tabla: `▦` abre sus datos, `⌬` su **estructura física**
  (niveles del B+ con sus separadores y ocupación, o profundidad global y cubetas del hash,
  y el archivo de valores de sus columnas `TEXT`),
  `ⓘ` la enfoca en el diagrama y `🗑` la elimina (con confirmación que avisa de cuántas
  filas e índices se pierden).
  El botón **Cargar CSV** abre el diálogo de ingesta, con dos modos. *Crear la tabla
  ahora*: se elige el archivo (arrastrándolo o con el selector), el nombre de la tabla
  —propuesto a partir del archivo— y una de cuatro organizaciones físicas:

  | Opción | Qué crea |
  |---|---|
  | **Sin índice** | heap file sin clave ni índices: toda búsqueda recorre la tabla |
  | **Heap file + índice hash** | heap con clave primaria y un índice hash sobre ella |
  | **B+ agrupado** | las filas viven en las hojas del árbol, ordenadas por la clave |
  | **Archivo secuencial** | ordenado por la clave, con desbordamiento y reorganización |

  «Sin índice» es el punto de partida para comparar: se ejecuta una consulta, se crea un
  índice con `CREATE INDEX` y se vuelve a ejecutar mirando el plan. En las otras tres se
  elige la columna clave de la cabecera del CSV. *Solo subir el archivo*: el CSV se guarda
  en el servidor y el editor recibe la plantilla `CREATE TABLE ... FROM FILE ... USING
  INDEX` para decidir la organización a mano con SQL. Los tipos se deducen leyendo el
  archivo. El botón `⌦` vacía la base de datos entera. Con la base vacía, el panel explica
  cómo poblarla.
- **Consultas** — editor con **numeración de líneas y resaltado de sintaxis**, `⌘↵` /
  `Ctrl↵` para ejecutar, `Tab` para indentar e historial de las últimas 25 consultas.
  Admite **varias sentencias separadas por `;`**: se ejecutan en orden y el panel muestra
  las filas de la última consulta. Debajo, **63 atajos** agrupados por bloque de la
  exposición —*Carga*, *Índices*, *Plan*, *Externos*, *SQL*, *Borrar*, *Transacciones*,
  *Espacial* y *Texto*—; un clic escribe la consulta en el editor. Todos se pueden lanzar varias veces sin error: los que
  crean o borran tablas usan `IF [NOT] EXISTS`, y los de *Borrar* trabajan sobre filas de
  prueba que insertan ellos mismos, así que no tocan el dataset. Para editarlos, ver
  [`src/snippets.ts`](src/snippets.ts).
- **Resultados** — *Filas* con la tabla de resultados (números alineados a la
  derecha, `NULL` en cursiva, cabecera fija al hacer scroll, columna `#` fija a la
  izquierda y celdas largas —un `TEXT` de varios KB— recortadas, con el valor completo al
  pasar el cursor), *Mensajes* con qué hizo cada sentencia del script y cuánto tardó, y *Mapa*
  cuando la consulta toca una columna `POINT` (ver más abajo). Botones
  para **copiar** al portapapeles y **exportar CSV**. Los errores salen aquí con su tipo y
  el **cursor bajo la posición exacta** que devuelve el parser.
- **Plan de ejecución** — el árbol de operadores. En **verde** el camino de acceso elegido
  (recorrido completo, índice hash, índice B+, R-Tree o el orden propio de la tabla) y en
  **naranja** los algoritmos externos que se apoyan en disco. Tras ejecutar, cada operador
  lleva las **filas reales** que produjo y su **tiempo inclusivo**: es lo mismo que
  devuelve `EXPLAIN ANALYZE`. Con `EXPLAIN` a secas se ve el plan sin ejecutar nada.

### El panel de mapa

La pestaña **Mapa** aparece en Resultados cuando la consulta toca una columna `POINT`, y
se abre sola si la consulta es espacial y devuelve puntos. El plan sigue visible al lado,
así que se ve a la vez **qué encontró** la consulta y **cómo** lo encontró.

| Capa | Qué es |
|---|---|
| puntos azules | las filas del resultado; un clic abre la ficha de la fila |
| rombo y contorno morados | la figura de la consulta: el radio, el polígono o el punto de referencia de un k-NN |
| puntos grises | todos los puntos de la tabla (una muestra de 5 000 si es mayor), para ver el resultado sobre el total |
| rectángulos verdes | los MBR de las hojas del R-Tree, con la casilla de la leyenda |

La leyenda dice cuántas filas hay en cada capa. Un radio euclidiano se dibuja como lo que
es —un círculo en grados, achatado sobre el terreno—, que es la forma más directa de ver
por qué esa métrica no sirve para distancias reales.

El mapa usa [Leaflet](https://leafletjs.com) con teselas de OpenStreetMap; la dirección de
las teselas sale de `VITE_MAP_TILES_URL`. Necesita conexión para el fondo, pero los puntos
y las figuras se dibujan igual sin ella.

### Esquema

Diagrama de las tablas con sus columnas, tipos, claves e índices, repartido en columnas por
dependencia para que las flechas no se crucen. Las flechas son **relaciones deducidas por
el nombre de la columna** (`curso_id` → `cursos.id`): el motor todavía no declara claves
foráneas, así que son una lectura del esquema y no una restricción que la base de datos
imponga. Al pie se listan todas.

## Texto

La página de la Parte 3. **Lo que se escribe es el texto de un `MATCH`, y cada búsqueda son
dos consultas SQL** que se ven lado a lado: la misma con `USING TF_IDF` y con `USING BM25`.
Todo va por `POST /query`; la página no tiene rutas del API propias, solo escribe el SQL que
se escribiría a mano en la consola, y ese SQL está siempre a la vista.

```
┌────────────────────────────────────────────────────────────────────────┐
│ catalogo.texto ▾ │ 🔍 zapatillas running mujer                          │
│ Términos tras el análisis  zapatill  running  muj                      │
│ [AND marca = 'PUMA' ×] [AND condición] [USING BM25 WITH k1 = b =] [LIMIT 10]│
├───────────────────────────────────┬────────────────────────────────────┤
│ TF-IDF + coseno  10 filas en 1.3 ms│ BM25  10 filas en 1.4 ms            │
│ SELECT *, SCORE() AS puntaje …    │ SELECT *, SCORE() AS puntaje …      │
│ #  Documento     Puntaje  Con BM25│ #  Documento    Puntaje  Con TF-IDF │
├───────────────────────────────────┴────────────────────────────────────┤
│ [Plan con TF-IDF + coseno] [Plan con BM25] [Índice idx_catalogo_texto] │
└────────────────────────────────────────────────────────────────────────┘
```

- **Dónde buscar.** Las columnas con índice `INVERTED`, leídas del catálogo (`/tables`). Si
  no hay ninguna, la página lo explica y ofrece crear el índice sobre una columna de texto
  (con el `CREATE INDEX` a la vista) o cargar el catálogo de demostración
  (`CREATE TABLE catalogo FROM FILE 'demos/samples/esci/productos.csv' USING INDEX
  INVERTED("texto")`, que necesita `demos/samples` en `MINIGESTOR_IMPORT_DIRS`).
- **Busca mientras se escribe** ([`useLiveSearch`](src/hooks/useLiveSearch.ts)): consulta
  200 ms después de la última tecla, o al pulsar `↵`. Las consultas van de una en una; si lo
  escrito cambió mientras una corría, al terminar se consulta lo último, y el resultado viejo
  queda atenuado hasta que llega el nuevo. Admite `AND`, `OR`, `NOT` y paréntesis (`funda AND
  iphone AND NOT silicona`, ver la [gramática](../src/sql/README.md#búsqueda-de-texto)).
- **Términos tras el análisis.** Los que devolvió el motor, plegados y reducidos a su raíz,
  con el mismo color que el resaltado de los resultados.
- **Las opciones son cláusulas del SQL**, escritas como SQL y con sus colores:
  - `AND columna = valor`, tantas como hagan falta (la primera empieza por la marca); el
    plan muestra el `Filter` sobre el `TextSearchScan`.
  - `USING BM25 WITH (k1, b)`: vacíos, la consulta no lleva `WITH` y el motor usa los suyos,
    que la respuesta trae (`text.k1`, `text.b`) y se ven como sugerencia. Un valor fuera de
    rango lo rechaza el motor y el error sale en la columna de BM25.
  - `LIMIT`, las filas de cada ranking.

  Una condición o un parámetro a medio escribir no dejan una consulta inválida: el SQL a la
  vista sigue siendo el último que se pudo ejecutar, y debajo se dice qué falta.
- **Cada ranking es su consulta y su resultado.** Arriba, el SQL exacto que se envió, con
  «Copiar» y «Abrir en la consola», que lo deja en el editor de la consola sin ejecutarlo
  (`openInConsole` de `useWorkspace`). Debajo, una tabla con cabeceras:
  - `#`, el puesto;
  - `Documento`, el título y un fragmento alrededor de la primera coincidencia, resaltados;
  - `Puntaje`, con los decimales de su ranking (el coseno vive entre 0 y 1; el de BM25
    crece con el idf) y una barra relativa al primero;
  - `Con BM25` o `Con TF-IDF`, el puesto del mismo documento en el otro ranking, o `—` si
    allí no salió.

  Si los dos rankings fallan por lo mismo (una consulta booleana mal escrita, por ejemplo),
  el error se ve una vez, encima de los dos.
- **Detrás de los rankings**, en pestañas: el plan de cada consulta con el `PlanView` de la
  consola (el `TextSearchScan` con sus términos, su df, los postings leídos y los
  documentos puntuados) y el índice invertido: documentos, vocabulario, postings,
  segmentos de SPIMI y el bloque en memoria, borrados, los términos con más df y el tamaño
  de cada archivo en disco. Es la misma vista que abre `⌬` en la consola.

### El resaltado

El API devuelve los términos **ya analizados** —plegados, sin stopwords y reducidos a su
raíz: «auriculares inalámbricos» llega como `auricular`, `inalambr`—, y en pantalla está el
texto original. [`lib/textHighlight.ts`](src/lib/textHighlight.ts) corta el texto en
palabras y cifras con la misma regla que el analizador del motor, pliega cada palabra igual
que él (NFKC, minúsculas, sin tildes ni diéresis salvo la ñ, sin apóstrofos) y marca las que
**empiezan por** una raíz; una cifra, solo si es igual a un término numérico. De una
consulta booleana el API manda solo los términos positivos: en `funda AND NOT silicona` se
marca «funda», no «silicona», y los operadores nunca, porque no son términos.

Es una aproximación, porque el stemmer de Snowball no se repite en el navegador:

- una raíz que el stemmer reescribe en vez de recortar («potencia» → `potent`) no marca la
  palabra;
- una raíz marca también palabras más largas que empiezan igual (`mochil` marca
  «mochilero»);
- una raíz de menos de tres letras solo marca la palabra idéntica, para que la `c` de
  «usb-c» no ilumine media ficha.

Las funciones son puras y tienen tests (`npm test`, ver abajo).

## Detalles de la interfaz

- **Tema claro y oscuro**, con el botón `☾` / `☀` de la cabecera. Arranca con el tema del
  sistema y recuerda la elección. Todos los colores son variables CSS; el tema oscuro solo
  las redefine, así que ningún componente sabe en qué tema está.
- **Foco visible** al navegar con teclado, y sin transiciones si el sistema pide reducir el
  movimiento.
- **Divisores arrastrables** entre la barra lateral y el área principal, y entre el editor
  y los resultados.
- **Adaptable**: por debajo de 1280 px el plan pasa debajo de los resultados; por debajo de
  1024 px la barra lateral se coloca arriba; por debajo de 720 px la cabecera se reordena.
  En ningún ancho aparece scroll horizontal de página.
- La insignia de la cabecera pasa a **Transacción abierta** en cuanto se ejecuta un `BEGIN`,
  y vuelve a *Autocommit* al confirmar o abortar.

## Instalación y arranque

```bash
cd frontend
npm install
cp .env.example .env      # VITE_API_URL es obligatoria: la dirección del API;
                          # las teselas del mapa también se configuran ahí
npm run dev               # http://localhost:5173
```

El API tiene que estar en marcha (ver [`src/api/README.md`](../src/api/README.md)). La URL
**no está incrustada en el código**: sale de `VITE_API_URL`, y si falta la interfaz lo dice en
el panel de resultados en vez de llamar a un `localhost` que quizás no existe.

Lo que la interfaz cuida además:

- **Sesión**: cada pestaña tiene un identificador aleatorio (`crypto.randomUUID`) y la cierra
  con `sendBeacon` al irse, que es la única petición que el navegador garantiza enviar
  mientras descarga la página.
- **Transacciones**: si un error del motor aborta la transacción —un interbloqueo, una espera
  agotada—, el indicador «Transacción abierta» se apaga, porque el error lo dice. Vale
  también para las lecturas de fondo que van por la sesión: la estructura física (⌬) y los
  puntos y MBR del mapa.
- **Una petición a la vez**: mientras una sentencia corre, ni ⌘↵ ni la vista previa ni una
  subida lanzan otra; una sentencia que espera un bloqueo no se envía dos veces.
- **Resultados grandes**: el API envía como mucho 10 000 filas; la tabla avisa «Mostrando N
  de M filas» cuando hay más.
- **Accesibilidad**: los diálogos son modales de verdad (`aria-modal`, Tab da la vuelta
  dentro del diálogo, se cierran con Escape o con un clic que empieza y termina en el fondo,
  y devuelven el foco), los botones ✕ tienen nombre y los divisores entre paneles se mueven
  también con las flechas del teclado.

```bash
npm run build     # comprobación de tipos + bundle de producción en dist/
npm run lint      # solo comprobación de tipos
npm test          # tests de lib/ con el ejecutor de Node, sin dependencias
```

`npm test` corre `tests/*.test.ts` con `node --test`, que lee TypeScript directamente desde
Node 22.18 (o 23.6): no hace falta instalar un ejecutor de tests. El resto de la interfaz
funciona con Node 18.

## Organización del código

```
src/
  App.tsx                las páginas: carga perezosa y las ya abiertas, montadas
  routes.ts              las páginas y su dirección tras el #
  api.ts                 único punto que habla con el API
  config.ts              direcciones externas, leídas de las variables de entorno
  types.ts               tipos compartidos, espejo de los esquemas del API
  snippets.ts            guion de demostración de la consola
  textExamples.ts        ejemplos de la página de texto y el catálogo de demostración
  pages/
    ConsolePage.tsx      la consola SQL: estado y disposición de sus paneles
    TextSearchPage.tsx   la búsqueda de texto: estado y disposición
  hooks/
    useWorkspace.ts      sesión, transacción, tablas, tema y una petición a la vez
    useHashRoute.ts      la página que pide la dirección
    useInvertedIndex.ts  forma del índice invertido de una columna
    useLiveSearch.ts     búsqueda mientras se escribe, de una consulta a la vez
    useSplitter.ts       divisores arrastrables
    useTheme.ts          tema claro u oscuro, recordado entre visitas
    useLeafletMap.ts     ciclo de vida del mapa de Leaflet
  lib/
    textHighlight.ts     resaltado de los términos analizados sobre el texto original
    textSearch.ts        objetivos de búsqueda, SQL de cada ranking y tarjetas
    bytes.ts             tamaños en B, KB y MB
    highlight.ts         tokenizador de SQL para el resaltado
    relations.ts         inferencia de relaciones y reparto en columnas
    exporting.ts         CSV y portapapeles
    points.ts            reconocer y escribir puntos geográficos
    spatial.ts           filas con ubicación, textos de la leyenda, ocupación del R-Tree
    csvFiles.ts          cabecera de un CSV y plantilla de CREATE TABLE
  components/
    TopBar.tsx           cabecera común: páginas, transacción y tema
    SqlEditor.tsx        editor con gutter y resaltado
    SqlBlock.tsx         SQL de solo lectura con el mismo resaltado
    FailureNotice.tsx    un error con su tipo y su posición
    Stat.tsx             una cifra con su etiqueta
    InvertedIndexView.tsx  forma del índice invertido (en ⌬ y en la página de texto)
    QueryPanel.tsx       cabecera del editor, historial y atajos
    FilesPanel.tsx       panel de archivos con filtro
    ResultsPanel.tsx     pestañas de filas, mensajes y mapa; exportación y errores
    MapPanel.tsx         mapa con el resultado, las figuras y los MBR
    PlanPanel.tsx        árbol del plan con leyenda (PlanView, también en la página de texto)
    StructureDialog.tsx  estructura física: B+, hash, R-Tree, invertido y valores TEXT
    UploadDialog.tsx     carga de CSV
    SchemaView.tsx       diagrama de esquema y relaciones
    StatusBar.tsx        barra de estado
    text/                la página de texto: búsqueda, cláusulas, rankings y detalle
  styles.css             los dos temas, por variables CSS
tests/                   tests de lib/ (node --test)
```

Los componentes no contienen lógica de negocio: reciben datos y los dibujan. El estado
compartido vive en `useWorkspace`, el de cada página en su componente de `pages/`, todas
las llamadas de red en `api.ts` y todo el cálculo en `lib/`.
