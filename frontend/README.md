# Frontend

Interfaz tipo IDE de base de datos, en React + TypeScript + Vite. Los cuatro paneles que
pide el enunciado están **visibles a la vez**, para que en una sola pantalla se vea la
consulta, sus filas y cómo se ejecutó.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Minigestor                                        ● Autocommit / TX  │
├──────────────┬───────────────────────────────────────────────────────┤
│ ARCHIVOS     │ CONSULTAS                                  [Ejecutar] │
│              │  editor SQL + atajos de ejemplo                       │
│ tablas       ├────────────────────────────┬──────────────────────────┤
│ columnas     │ RESULTADOS                 │ PLAN DE EJECUCIÓN        │
│ índices      │  tabla de filas            │  árbol de operadores     │
├──────────────┴────────────────────────────┴──────────────────────────┤
│ Motor conectado · Tablas 2 · Filas 8 · Tiempo 2.17 ms · Sesión ui-…  │
└──────────────────────────────────────────────────────────────────────┘
```

## Qué muestra cada panel

- **Archivos** — las tablas cargadas y su estructura: organización física (heap file,
  secuencial o B+ agrupado), número de filas, columnas con su tipo, marca de clave primaria,
  `NOT NULL` y **con qué índice está indexada cada columna**. Un clic en `SELECT *` escribe
  la consulta en el editor.
- **Consultas** — editor SQL con `⌘↵` / `Ctrl↵` para ejecutar y una fila de atajos que
  recorren todo lo que sabe hacer la Parte 1: crear tablas con las tres organizaciones,
  índices hash y B+, rangos, `ORDER BY`, `GROUP BY`, `JOIN` y transacciones.
- **Resultados** — las filas devueltas, con números alineados a la derecha, `NULL` marcado
  en cursiva y el conteo y el tiempo en la cabecera. Los errores aparecen aquí con su tipo y
  su **línea y columna**.
- **Plan de ejecución** — el árbol de operadores de abajo hacia arriba. El **camino de
  acceso** va resaltado en verde, que es la decisión interesante: si la consulta terminó
  usando el índice hash, el índice B+, el orden propio de la tabla o un recorrido completo.

La barra de estado indica si el motor responde, cuántas tablas hay, filas y tiempo de la
última consulta, y el identificador de sesión. La insignia de arriba a la derecha cambia a
**Transacción abierta** en cuanto se ejecuta un `BEGIN`.

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
  App.tsx              estado de la aplicación y disposición de los paneles
  api.ts               único punto que habla con el API
  types.ts             tipos compartidos, espejo de los esquemas del API
  snippets.ts          consultas de ejemplo
  components/
    FilesPanel.tsx     panel de archivos
    QueryPanel.tsx     editor de consultas
    ResultsPanel.tsx   tabla de resultados y errores
    PlanPanel.tsx      árbol del plan de ejecución
    StatusBar.tsx      barra de estado
  styles.css           tema oscuro por variables CSS
```

Los componentes no contienen lógica de negocio: reciben datos y los dibujan. Todo el estado
vive en `App.tsx` y todas las llamadas de red en `api.ts`.
