export interface Snippet {
  label: string;
  description: string;
  sql: string;
}

export interface SnippetGroup {
  name: string;
  snippets: Snippet[];
}

/**
 * Guion de demostración sobre el dataset de e-commerce (`demos/poblar_ecommerce.py`, o sus
 * CSV de `demos/samples/`). Los grupos siguen el orden de la exposición y cada atajo se
 * puede lanzar varias veces sin error: los que crean o borran algo usan IF [NOT] EXISTS.
 */
export const SNIPPET_GROUPS: SnippetGroup[] = [
  {
    name: "Carga",
    snippets: [
      {
        label: "Índices tras cargar CSV",
        description:
          "Tras subir los CSV de demos/samples/: crea los dos índices secundarios del dataset",
        sql: `CREATE INDEX IF NOT EXISTS idx_clientes_ciudad
  ON clientes USING HASH (ciudad);

CREATE INDEX IF NOT EXISTS idx_detalle_pedidos_pedido_id
  ON detalle_pedidos USING BTREE (pedido_id);`,
      },
      {
        label: "Crear tabla",
        description:
          "Una tabla heap por SQL: su clave recibe un B+ automático y producto_id un B+ secundario",
        sql: `DROP TABLE IF EXISTS resenas;

CREATE TABLE resenas (
  id INT PRIMARY KEY,
  producto_id INT INDEX BTREE,
  cliente_id INT,
  puntaje INT,
  comentario VARCHAR(80)
);

INSERT INTO resenas VALUES
  (1, 10, 5, 5, 'Excelente'),
  (2, 10, 8, 4, 'Buena relación precio-calidad'),
  (3, 42, 3, 2, 'Llegó tarde');

SELECT * FROM resenas WHERE producto_id = 10;`,
      },
    ],
  },
  {
    name: "Índices",
    snippets: [
      {
        label: "Índice hash",
        description: "Igualdad sobre una columna con índice hash: el plan muestra IndexLookup",
        sql: "SELECT * FROM clientes WHERE ciudad = 'Cusco' LIMIT 30;",
      },
      {
        label: "Sin índice",
        description: "La misma tabla por una columna sin índice: recorrido completo, y se nota",
        sql: "SELECT * FROM clientes WHERE nombre = 'Renzo Cárdenas';",
      },
      {
        label: "Crear índice",
        description: "La misma consulta tras indexar nombre: el plan pasa a IndexLookup",
        sql: `CREATE INDEX IF NOT EXISTS idx_clientes_nombre ON clientes USING HASH (nombre);
SELECT * FROM clientes WHERE nombre = 'Renzo Cárdenas';`,
      },
      {
        label: "Quitar índice",
        description: "Se borra el índice y la misma consulta vuelve al recorrido completo",
        sql: `DROP INDEX IF EXISTS idx_clientes_nombre;
SELECT * FROM clientes WHERE nombre = 'Renzo Cárdenas';`,
      },
      {
        label: "Rango B+ agrupado",
        description: "productos guarda sus filas dentro del árbol: el rango no salta al heap",
        sql: "SELECT * FROM productos WHERE id BETWEEN 100 AND 140;",
      },
      {
        label: "Rango secuencial",
        description: "pedidos está ordenado por id: aprovecha su propio orden, sin índice extra",
        sql: "SELECT * FROM pedidos WHERE id BETWEEN 500 AND 560;",
      },
      {
        label: "B+ secundario",
        description: "detalle_pedidos: índice B+ sobre pedido_id más el salto al heap",
        sql: "SELECT * FROM detalle_pedidos WHERE pedido_id = 4210;",
      },
    ],
  },
  {
    name: "Plan",
    snippets: [
      {
        label: "EXPLAIN",
        description: "El plan elegido, sin ejecutar la consulta: qué camino de acceso se usaría",
        sql: `EXPLAIN
SELECT c.nombre, p.total
FROM pedidos AS p
JOIN clientes AS c ON p.cliente_id = c.id
WHERE c.ciudad = 'Cusco'
ORDER BY p.total DESC
LIMIT 5;`,
      },
      {
        label: "EXPLAIN ANALYZE",
        description: "La ejecuta y enseña filas reales y tiempo de cada operador",
        sql: `EXPLAIN ANALYZE
SELECT c.nombre, p.total
FROM pedidos AS p
JOIN clientes AS c ON p.cliente_id = c.id
WHERE c.ciudad = 'Cusco'
ORDER BY p.total DESC
LIMIT 5;`,
      },
    ],
  },
  {
    name: "Externos",
    snippets: [
      {
        label: "GROUP BY",
        description: "Agrupación con hashing externo",
        sql: `SELECT estado, COUNT(*) AS pedidos, AVG(total) AS ticket_promedio
FROM pedidos
GROUP BY estado
ORDER BY pedidos DESC;`,
      },
      {
        label: "HAVING",
        description: "Filtra grupos después de agregar; la agregación se calcula una sola vez",
        sql: `SELECT estado, COUNT(*) AS pedidos
FROM pedidos
GROUP BY estado
HAVING COUNT(*) > 1100
ORDER BY pedidos DESC;`,
      },
      {
        label: "ORDER BY",
        description: "Ordenamiento externo por mezcla k-vías",
        sql: `SELECT nombre, precio, stock
FROM productos
ORDER BY precio DESC
LIMIT 20;`,
      },
      {
        label: "Orden completo",
        description: "6 000 filas sin LIMIT: el plan dice cuántos runs y pasadas de mezcla hubo",
        sql: "SELECT id, fecha, total FROM pedidos ORDER BY total DESC;",
      },
      {
        label: "JOIN de 3 tablas",
        description: "Grace hash join encadenado; el WHERE baja hasta el índice de clientes",
        sql: `SELECT c.nombre, c.ciudad, pr.nombre, d.cantidad, d.precio_unitario
FROM detalle_pedidos AS d
JOIN pedidos AS p ON d.pedido_id = p.id
JOIN clientes AS c ON p.cliente_id = c.id
JOIN productos AS pr ON d.producto_id = pr.id
WHERE c.ciudad = 'Arequipa'
ORDER BY d.precio_unitario DESC
LIMIT 25;`,
      },
    ],
  },
  {
    name: "SQL",
    snippets: [
      {
        label: "Filtros",
        description: "LIKE, IN e IS NULL sobre el mismo recorrido",
        sql: `SELECT nombre, ciudad, email
FROM clientes
WHERE ciudad IN ('Lima', 'Cusco')
  AND nombre LIKE 'A%'
  AND email IS NOT NULL
LIMIT 30;`,
      },
      {
        label: "DISTINCT",
        description: "Elimina repetidos comparando la fila completa",
        sql: "SELECT DISTINCT ciudad FROM clientes ORDER BY ciudad;",
      },
      {
        label: "UPDATE y DELETE",
        description: "Modifica y borra; en el secuencial el borrado es lazy y deja lápidas",
        sql: `UPDATE productos SET stock = stock + 50 WHERE id BETWEEN 1 AND 10;
SELECT id, nombre, stock FROM productos WHERE id BETWEEN 1 AND 10;

DELETE FROM pedidos WHERE estado = 'cancelado';
SELECT estado, COUNT(*) AS pedidos FROM pedidos GROUP BY estado;`,
      },
    ],
  },
  {
    name: "Transacciones",
    snippets: [
      {
        label: "BEGIN + UPDATE",
        description: "Abre una transacción y cambia una fila: la insignia pasa a Transacción abierta",
        sql: `BEGIN TRANSACTION;
UPDATE productos SET stock = 0 WHERE id = 1;
SELECT id, nombre, stock FROM productos WHERE id = 1;`,
      },
      {
        label: "ROLLBACK",
        description: "Deshace la transacción: el stock vuelve a su valor anterior",
        sql: `ROLLBACK;
SELECT id, nombre, stock FROM productos WHERE id = 1;`,
      },
      {
        label: "COMMIT",
        description: "Confirma la transacción y libera sus bloqueos",
        sql: "COMMIT;",
      },
      {
        label: "Otra pestaña: esperar",
        description:
          "En una segunda pestaña, con la primera en BEGIN + UPDATE: espera el bloqueo de tabla",
        sql: `UPDATE productos SET stock = 99 WHERE id = 2;
SELECT id, nombre, stock FROM productos WHERE id = 2;`,
      },
    ],
  },
];
