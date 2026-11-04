# Frontend

Interfaz tipo IDE de base de datos, en React + TypeScript + Vite. Los cuatro paneles que
pide el enunciado están **visibles a la vez**, para que en una sola pantalla se vea la
consulta, sus filas y cómo se ejecutó.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Minigestor   [Consulta] [Esquema]        ● Autocommit   [Actualizar] │
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

## Las dos vistas

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
  las filas de la última consulta. Debajo, **51 atajos** agrupados por bloque de la
  exposición —*Carga*, *Índices*, *Plan*, *Externos*, *SQL*, *Borrar*, *Transacciones*
  y *Espacial*—; un clic escribe la consulta en el editor. Todos se pueden lanzar varias veces sin error: los que
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
```

## Organización del código

```
src/
  App.tsx                estado de la aplicación y disposición de los paneles
  api.ts                 único punto que habla con el API
  config.ts              direcciones externas, leídas de las variables de entorno
  types.ts               tipos compartidos, espejo de los esquemas del API
  snippets.ts            guion de demostración
  hooks/
    useSplitter.ts       divisores arrastrables
    useTheme.ts          tema claro u oscuro, recordado entre visitas
    useLeafletMap.ts     ciclo de vida del mapa de Leaflet
  lib/
    highlight.ts         tokenizador de SQL para el resaltado
    relations.ts         inferencia de relaciones y reparto en columnas
    exporting.ts         CSV y portapapeles
    points.ts            reconocer y escribir puntos geográficos
    spatial.ts           filas con ubicación, textos de la leyenda, ocupación del R-Tree
    csvFiles.ts          cabecera de un CSV y plantilla de CREATE TABLE
  components/
    SqlEditor.tsx        editor con gutter y resaltado
    QueryPanel.tsx       cabecera del editor, historial y atajos
    FilesPanel.tsx       panel de archivos con filtro
    ResultsPanel.tsx     pestañas de filas, mensajes y mapa; exportación y errores
    MapPanel.tsx         mapa con el resultado, las figuras y los MBR
    PlanPanel.tsx        árbol del plan con leyenda
    StructureDialog.tsx  estructura física: B+, hash, R-Tree y valores TEXT
    UploadDialog.tsx     carga de CSV
    SchemaView.tsx       diagrama de esquema y relaciones
    StatusBar.tsx        barra de estado
  styles.css             los dos temas, por variables CSS
```

Los componentes no contienen lógica de negocio: reciben datos y los dibujan. Todo el estado
vive en `App.tsx`, todas las llamadas de red en `api.ts` y todo el cálculo en `lib/`.
