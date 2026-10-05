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
 *
 * Los del grupo «Espacial» usan la tabla `tiendas`. Sus coordenadas son las del generador:
 * la Plaza de Armas de Lima, el Parque Kennedy y el contorno simplificado de Miraflores.
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
      {
        label: "ORDER BY sin ordenar",
        description:
          "pedidos es un archivo secuencial: ya está en orden de clave, así que el plan no lleva ExternalSort",
        sql: `EXPLAIN ANALYZE
SELECT id, fecha, total
FROM pedidos
ORDER BY id
LIMIT 10;`,
      },
      {
        label: "Constantes",
        description:
          "Una expresión constante vale como un literal: el rango se calcula una vez y va al índice",
        sql: `EXPLAIN ANALYZE
SELECT id, cliente_id, total
FROM pedidos
WHERE id BETWEEN 10 * 10 AND 10 * 10 + 20;`,
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
      {
        label: "LEFT JOIN",
        description:
          "Clientes sin ningún pedido: la reunión externa los conserva con NULL y COUNT no los cuenta",
        sql: `SELECT c.id, c.nombre, c.ciudad, COUNT(p.id) AS pedidos
FROM clientes AS c
LEFT JOIN pedidos AS p ON p.cliente_id = c.id
GROUP BY c.id, c.nombre, c.ciudad
HAVING COUNT(p.id) = 0
ORDER BY c.id
LIMIT 25;`,
      },
      {
        label: "JOIN sin igualdad",
        description:
          "Sin una igualdad no hay clave de hash: bucles anidados en bloques (NestedLoopJoin en el plan)",
        sql: `SELECT a.nombre AS categoria, b.nombre AS siguiente
FROM categorias AS a
JOIN categorias AS b ON a.id < b.id AND b.id <= a.id + 2
ORDER BY a.id, b.id;`,
      },
      {
        label: "GROUP BY expresión",
        description:
          "Se agrupa por una expresión, nombrada por su posición; las agregaciones se combinan entre sí",
        sql: `SELECT total >= 1000 AS pedido_grande,
       COUNT(*) AS pedidos,
       SUM(total) / COUNT(*) AS ticket_medio,
       MAX(total) - MIN(total) AS amplitud
FROM pedidos
GROUP BY 1
ORDER BY 1;`,
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
        description: "La primera aparición de cada resultado, en su orden; en disco si no caben en memoria",
        sql: "SELECT DISTINCT ciudad FROM clientes ORDER BY ciudad;",
      },
      {
        label: "Fechas",
        description: "Una fecha se escribe como texto ISO y se compara como fecha, no como texto",
        sql: `SELECT id, cliente_id, fecha, total
FROM pedidos
WHERE fecha BETWEEN '2025-01-01' AND '2025-01-31'
ORDER BY fecha
LIMIT 30;`,
      },
      {
        label: "NULL e índices",
        description:
          "Un NULL se guarda aunque la columna tenga índice: el índice lo deja fuera e IS NULL lo encuentra",
        sql: `DELETE FROM clientes WHERE id = 900001;
INSERT INTO clientes VALUES (900001, 'Cliente sin ciudad', 'prueba@correo.pe', NULL, '2026-03-15');

SELECT id, nombre, ciudad, fecha_alta FROM clientes WHERE ciudad IS NULL;

DELETE FROM clientes WHERE id = 900001;`,
      },
      {
        label: "UNIQUE y clave",
        description:
          "Una columna UNIQUE no se repite (varios NULL sí); un UPDATE puede cambiar la clave de todas las filas a la vez",
        sql: `DROP TABLE IF EXISTS usuarios;

CREATE TABLE usuarios (
  id     INT PRIMARY KEY,
  correo VARCHAR(40) UNIQUE,
  alias  VARCHAR(20)
);

INSERT INTO usuarios VALUES
  (1, 'ana@correo.pe', 'ana'),
  (2, 'luis@correo.pe', 'luis'),
  (3, NULL, 'sin correo'),
  (4, NULL, 'tampoco');

UPDATE usuarios SET id = id + 1;

SELECT * FROM usuarios ORDER BY id;`,
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
  {
    name: "Espacial",
    snippets: [
      {
        label: "Tabla con puntos",
        description:
          "Columna POINT con índice R-Tree declarado al crearla; POINT(latitud, longitud)",
        sql: `DROP TABLE IF EXISTS sedes;

CREATE TABLE sedes (
  id INT PRIMARY KEY,
  nombre VARCHAR(30),
  ubicacion POINT INDEX RTREE
);

INSERT INTO sedes VALUES
  (1, 'UTEC', POINT(-12.1352, -77.0222)),
  (2, 'Plaza de Armas', POINT(-12.0464, -77.0428)),
  (3, 'Aeropuerto Jorge Chávez', POINT(-12.0219, -77.1143));

SELECT nombre, ubicacion,
       distancia(ubicacion, POINT(-12.1352, -77.0222)) AS metros
FROM sedes
ORDER BY metros;`,
      },
      {
        label: "Radio 5 km",
        description:
          "Tiendas a menos de 5 km de la Plaza de Armas: SpatialRangeScan y nodos visitados",
        sql: `SELECT id, nombre, distrito, ubicacion
FROM tiendas
WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;`,
      },
      {
        label: "10 más cercanas",
        description: "k-NN: el R-Tree entrega las filas por cercanía, sin ordenar nada",
        sql: `SELECT nombre, rubro,
       distancia(ubicacion, POINT(-12.0464, -77.0428)) AS metros,
       ubicacion
FROM tiendas
ORDER BY metros
LIMIT 10;`,
      },
      {
        label: "Gasolineras cercanas",
        description:
          "k-NN con filtro, desde el Parque Kennedy: el índice da el orden y el WHERE descarta",
        sql: `SELECT nombre, distrito,
       distancia(ubicacion, POINT(-12.1211, -77.0297)) AS metros,
       ubicacion
FROM tiendas
WHERE rubro = 'gasolinera'
ORDER BY metros
LIMIT 10;`,
      },
      {
        label: "Dentro de Miraflores",
        description: "Intersección con un polígono: SpatialPolygonScan",
        sql: `SELECT id, nombre, distrito, ubicacion
FROM tiendas
WHERE intersecta(ubicacion, POLYGON(
  (-12.112, -77.046), (-12.112, -77.010), (-12.136, -77.008),
  (-12.136, -77.026), (-12.126, -77.036)));`,
      },
      {
        label: "Tiendas por distrito",
        description: "El filtro espacial alimenta un GROUP BY: R-Tree más hashing externo",
        sql: `SELECT distrito, COUNT(*) AS tiendas
FROM tiendas
WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000
GROUP BY distrito
ORDER BY tiendas DESC;`,
      },
      {
        label: "Haversine y euclidiana",
        description: "Las dos métricas sobre las mismas filas: metros frente a grados",
        sql: `SELECT nombre,
       distancia(ubicacion, POINT(-12.0464, -77.0428)) AS metros,
       distancia(ubicacion, POINT(-12.0464, -77.0428), metrica='euclidiana') AS grados,
       ubicacion
FROM tiendas
WHERE distancia(ubicacion, POINT(-12.0464, -77.0428), metrica='euclidiana') < 0.02;`,
      },
      {
        label: "Sin R-Tree",
        description: "Se quita el índice y la misma consulta recorre toda la tabla",
        sql: `DROP INDEX IF EXISTS idx_tiendas_ubicacion;

SELECT id, nombre, distrito, ubicacion
FROM tiendas
WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;`,
      },
      {
        label: "Crear R-Tree",
        description: "Carga masiva del índice y la misma consulta: vuelve SpatialRangeScan",
        sql: `CREATE INDEX IF NOT EXISTS idx_tiendas_ubicacion
  ON tiendas USING RTREE (ubicacion);

SELECT id, nombre, distrito, ubicacion
FROM tiendas
WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;`,
      },
      {
        label: "EXPLAIN k-NN",
        description: "Nodos del R-Tree abiertos para encontrar 10 vecinos",
        sql: `EXPLAIN ANALYZE
SELECT nombre, ubicacion
FROM tiendas
ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428))
LIMIT 10;`,
      },
    ],
  },
];
