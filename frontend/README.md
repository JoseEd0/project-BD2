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
  columna, y tres botones por tabla: `▦` abre sus datos, `ⓘ` la enfoca en el diagrama y
  `🗑` la elimina (con confirmación que avisa de cuántas filas e índices se pierden).
  El botón **Cargar CSV** abre el diálogo de ingesta: se elige el archivo, el nombre de la
  tabla —que se propone a partir del nombre del archivo— y la organización física; los
  tipos de cada columna se deducen leyendo el archivo.
- **Consultas** — editor con **numeración de líneas y resaltado de sintaxis**, `⌘↵` /
  `Ctrl↵` para ejecutar, `Tab` para indentar, historial de las últimas 25 consultas y una
  fila de atajos que recorre todo lo que sabe hacer la Parte 1. Admite **varias sentencias
  separadas por `;`**: se ejecutan en orden y el panel muestra las filas de la última
  consulta.
- **Resultados** — dos pestañas. *Filas* con la tabla de resultados (números alineados a la
  derecha, `NULL` en cursiva, cabecera fija al hacer scroll y columna `#` fija a la
  izquierda), y *Mensajes* con qué hizo cada sentencia del script y cuánto tardó. Botones
  para **copiar** al portapapeles y **exportar CSV**. Los errores salen aquí con su tipo y
  el **cursor bajo la posición exacta** que devuelve el parser.
- **Plan de ejecución** — el árbol de operadores. En **verde** el camino de acceso elegido
  (recorrido completo, índice hash, índice B+ o el orden propio de la tabla) y en
  **naranja** los algoritmos externos que se apoyan en disco.

### Esquema

Diagrama de las tablas con sus columnas, tipos, claves e índices, repartido en columnas por
dependencia para que las flechas no se crucen. Las flechas son **relaciones deducidas por
el nombre de la columna** (`curso_id` → `cursos.id`): el motor todavía no declara claves
foráneas, así que son una lectura del esquema y no una restricción que la base de datos
imponga. Al pie se listan todas.

## Detalles de la interfaz

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
cp .env.example .env      # ajusta VITE_API_URL si el API no está en el puerto 8000
npm run dev               # http://localhost:5173
```

El API tiene que estar en marcha (ver [`src/api/README.md`](../src/api/README.md)). La URL
**no está incrustada en el código**: sale de `VITE_API_URL`.

```bash
npm run build     # comprobación de tipos + bundle de producción en dist/
npm run lint      # solo comprobación de tipos
```

## Organización del código

```
src/
  App.tsx                estado de la aplicación y disposición de los paneles
  api.ts                 único punto que habla con el API
  types.ts               tipos compartidos, espejo de los esquemas del API
  snippets.ts            guion de demostración
  hooks/
    useSplitter.ts       divisores arrastrables
  lib/
    highlight.ts         tokenizador de SQL para el resaltado
    relations.ts         inferencia de relaciones y reparto en columnas
    exporting.ts         CSV y portapapeles
  components/
    SqlEditor.tsx        editor con gutter y resaltado
    QueryPanel.tsx       cabecera del editor, historial y atajos
    FilesPanel.tsx       panel de archivos con filtro
    ResultsPanel.tsx     pestañas de filas y mensajes, exportación, errores
    PlanPanel.tsx        árbol del plan con leyenda
    SchemaView.tsx       diagrama de esquema y relaciones
    StatusBar.tsx        barra de estado
  styles.css             tema oscuro por variables CSS
```

Los componentes no contienen lógica de negocio: reciben datos y los dibujan. Todo el estado
vive en `App.tsx`, todas las llamadas de red en `api.ts` y todo el cálculo en `lib/`.
