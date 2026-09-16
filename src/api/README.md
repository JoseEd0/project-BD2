# API REST

Capa delgada sobre el motor: traduce HTTP a sentencias y devuelve JSON. **No contiene
lógica de base de datos**; si algo hay que decidir sobre datos, lo decide el motor.

## Arrancar

```bash
.venv/bin/pip install -e .
MINIGESTOR_DATA_DIR=./data .venv/bin/uvicorn api.main:app --reload --port 8000
```

Documentación interactiva generada por FastAPI en `http://localhost:8000/docs`.

| Variable de entorno | Para qué | Por defecto |
|---|---|---|
| `MINIGESTOR_DATA_DIR` | dónde viven los archivos del gestor | `data` |
| `MINIGESTOR_CORS_ORIGINS` | orígenes permitidos, separados por comas | `http://localhost:5173` |
| `MINIGESTOR_LOCK_TIMEOUT` | segundos que una transacción espera un bloqueo | `5` |

Ninguna ruta ni puerto está escrito en el código.

## Rutas

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | comprobar que el servidor responde |
| `GET` | `/tables` | panel de archivos: tablas, columnas, tipos, índices y número de filas |
| `POST` | `/query` | ejecuta un script y devuelve filas, mensajes y plan |
| `POST` | `/tables/upload` | crea una tabla a partir de un CSV subido |
| `POST` | `/files/upload` | solo guarda el CSV; devuelve su ruta y su cabecera para usarla en `CREATE TABLE ... FROM FILE` |
| `GET` | `/tables/{name}/structure` | estructura física: páginas del archivo, niveles del B+, profundidad global y cubetas del hash |
| `DELETE` | `/tables` | borra todas las tablas y sus archivos (dejar la BD vacía) |
| `DELETE` | `/sessions/{id}` | cierra una sesión y aborta lo que tuviera abierto |

### Sesiones y transacciones

`POST /query` acepta un `session_id` opcional:

- **sin él**, la sentencia va en autocommit;
- **con él**, el servidor recuerda la sesión entre peticiones, así que `BEGIN … COMMIT`
  funciona a través de varias llamadas. Eso es lo que permite demostrar transacciones desde
  la interfaz.

### Scripts de varias sentencias

El cuerpo puede traer varias sentencias separadas por `;`. Se ejecutan **en orden**; si una
falla, las anteriores ya quedaron aplicadas, igual que en cualquier gestor fuera de una
transacción. La respuesta trae un resumen por sentencia y, en el nivel superior, **las filas
y el plan de la última consulta**, que es lo que el editor enseña.

```jsonc
// POST /query
{ "sql": "INSERT INTO alumnos VALUES (1, 'Ana'); SELECT * FROM alumnos;",
  "session_id": "ui-a1b2c3" }

// 200 OK
{
  "statements": [
    { "sql": "Insert", "message": "1 fila(s) insertada(s) en 'alumnos'",
      "affected_rows": 1, "elapsed_ms": 0.31, "returned_rows": 0 },
    { "sql": "Select", "message": "", "affected_rows": 1,
      "elapsed_ms": 0.11, "returned_rows": 1 }
  ],
  "columns": ["id", "nombre"],
  "rows": [[1, "Ana"]],
  "plan": { "operation": "Projection", "detail": "id, nombre", "children": [ ... ] },
  "message": "",
  "affected_rows": 1,
  "elapsed_ms": 0.42,
  "in_transaction": false
}
```

### Carga de CSV

`POST /tables/upload` recibe un formulario *multipart*:

| Campo | Qué es |
|---|---|
| `file` | el CSV, con cabecera; los tipos se deducen leyendo el archivo entero |
| `name` | nombre de la tabla; solo se admite un identificador |
| `organization` | `heap`, `sequential` o `clustered_btree` |
| `key_column` | columna clave: obligatoria en `sequential` y `clustered_btree`; opcional en `heap`, donde recibe un índice hash |
| `session_id` | sesión, igual que en `/query` |

La sentencia se construye como objeto, **no concatenando texto SQL**: un nombre de tabla
malicioso no puede inyectar nada. La carga es atómica: si una fila falla —una clave
repetida, una columna clave que no está en la cabecera— la tabla no queda creada.

### Errores

Los errores de SQL devuelven `400` con la posición cuando el parser la conoce, que es lo
que el editor del frontend necesita para señalar dónde está el problema:

```jsonc
{ "detail": { "error": "expected a statement, found 'FROM'",
              "kind": "SqlSyntaxError", "line": 1, "column": 8 } }
```

Solo las excepciones **de dominio** —errores del SQL, del catálogo, del almacenamiento, de
la carga de archivos o de bloqueos— se devuelven como `400`: son culpa de lo que se pidió.
Cualquier otra excepción es un fallo del servidor y llega como `500`; convertirla en un
`400` escondería el bug detrás de un «error del usuario».

## Tests

```bash
.venv/bin/python -m pytest tests/api -q
```

Cubren las cuatro rutas, la estructura que consume el panel de archivos, el plan devuelto
en un `SELECT`, los errores con posición y el ciclo completo de una transacción a través de
varias peticiones (incluido el rollback al cerrar la sesión).
