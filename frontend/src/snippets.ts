export interface Snippet {
  label: string;
  description: string;
  sql: string;
}

/**
 * Guion de demostración sobre el dataset de e-commerce (`demos/poblar_ecommerce.py`).
 * Cada atajo enseña una pieza distinta de la Parte 1; el orden está pensado para
 * recorrerlos de izquierda a derecha durante la exposición.
 */
export const SNIPPETS: Snippet[] = [
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
    label: "Índice B+ secundario",
    description: "detalle_pedidos: índice B+ sobre pedido_id más el salto al heap",
    sql: "SELECT * FROM detalle_pedidos WHERE pedido_id = 4210;",
  },
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
  {
    label: "Orden completo",
    description: "6 000 filas sin LIMIT: el ordenamiento externo genera runs y los mezcla",
    sql: "SELECT id, fecha, total FROM pedidos ORDER BY total DESC;",
  },
  {
    label: "UPDATE y DELETE",
    description: "Modifica y borra; en el secuencial el borrado es lazy y deja lápidas",
    sql: `UPDATE productos SET stock = stock + 50 WHERE id BETWEEN 1 AND 10;
SELECT id, nombre, stock FROM productos WHERE id BETWEEN 1 AND 10;

DELETE FROM pedidos WHERE estado = 'cancelado';
SELECT estado, COUNT(*) AS pedidos FROM pedidos GROUP BY estado;`,
  },
  {
    label: "Crear índice",
    description: "Crea el índice y vuelve a lanzar la consulta: el plan cambia",
    sql: `CREATE INDEX idx_pedidos_estado ON pedidos USING HASH (estado);
SELECT * FROM pedidos WHERE estado = 'entregado' LIMIT 30;`,
  },
  {
    label: "Transacción",
    description: "BEGIN, cambio y ROLLBACK: la fila vuelve a su valor anterior",
    sql: `BEGIN TRANSACTION;
UPDATE productos SET stock = 0 WHERE id = 1;
SELECT id, nombre, stock FROM productos WHERE id = 1;`,
  },
  {
    label: "Crear tabla",
    description: "Las tres organizaciones físicas en un solo script",
    sql: `CREATE TABLE resenas (
  id INT PRIMARY KEY INDEX BTREE,
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
];
