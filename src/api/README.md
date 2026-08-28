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

Ninguna ruta ni puerto está escrito en el código.

## Rutas

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | comprobar que el servidor responde |
| `GET` | `/tables` | panel de archivos: tablas, columnas, tipos, índices y número de filas |
| `POST` | `/query` | ejecuta una sentencia y devuelve filas, mensaje y plan |
| `DELETE` | `/sessions/{id}` | cierra una sesión y aborta lo que tuviera abierto |

### Sesiones y transacciones

`POST /query` acepta un `session_id` opcional:

- **sin él**, la sentencia va en autocommit;
- **con él**, el servidor recuerda la sesión entre peticiones, así que `BEGIN … COMMIT`
  funciona a través de varias llamadas. Eso es lo que permite demostrar transacciones desde
  la interfaz.

```jsonc
// POST /query
{ "sql": "SELECT * FROM alumnos WHERE id = 1", "session_id": "ui-a1b2c3" }

// 200 OK
{
  "columns": ["id", "nombre"],
  "rows": [[1, "Ana"]],
  "plan": { "operation": "Projection", "detail": "id, nombre", "children": [ ... ] },
  "message": "",
  "affected_rows": 1,
  "elapsed_ms": 0.42,
  "in_transaction": false
}
```

### Errores

Los errores de SQL devuelven `400` con la posición cuando el parser la conoce, que es lo
que el editor del frontend necesita para señalar dónde está el problema:

```jsonc
{ "detail": { "error": "expected a statement, found 'FROM'",
              "kind": "SqlSyntaxError", "line": 1, "column": 8 } }
```

## Tests

```bash
.venv/bin/python -m pytest tests/api -q
```

Cubren las cuatro rutas, la estructura que consume el panel de archivos, el plan devuelto
en un `SELECT`, los errores con posición y el ciclo completo de una transacción a través de
varias peticiones (incluido el rollback al cerrar la sesión).
