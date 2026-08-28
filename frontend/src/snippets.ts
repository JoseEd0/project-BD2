/** Consultas de ejemplo que recorren lo que la Parte 1 sabe hacer. */
export const SNIPPETS: { label: string; sql: string }[] = [
  {
    label: "CREATE TABLE",
    sql: `CREATE TABLE alumnos (
  id INT PRIMARY KEY,
  nombre VARCHAR(30),
  ciudad VARCHAR(20) INDEX HASH,
  nota FLOAT
);`,
  },
  {
    label: "Heap vs secuencial vs B+",
    sql: `CREATE TABLE t_heap (id INT PRIMARY KEY, v VARCHAR(20));
CREATE TABLE t_seq  (id INT PRIMARY KEY INDEX SEQ, v VARCHAR(20));
CREATE TABLE t_bpt  (id INT PRIMARY KEY INDEX BTREE, v VARCHAR(20));`,
  },
  {
    label: "INSERT",
    sql: `INSERT INTO alumnos VALUES
  (1, 'Ana',  'lima',  18.5),
  (2, 'Luis', 'cusco', 15.0),
  (3, 'Sara', 'lima',  17.25);`,
  },
  {
    label: "WHERE + índice hash",
    sql: "SELECT * FROM alumnos WHERE ciudad = 'lima';",
  },
  {
    label: "Rango con B+",
    sql: "SELECT * FROM alumnos WHERE id BETWEEN 1 AND 3;",
  },
  {
    label: "ORDER BY (sort externo)",
    sql: "SELECT nombre, nota FROM alumnos ORDER BY nota DESC LIMIT 10;",
  },
  {
    label: "GROUP BY (hash externo)",
    sql: `SELECT ciudad, COUNT(*) AS total, AVG(nota) AS promedio
FROM alumnos
GROUP BY ciudad
ORDER BY total DESC;`,
  },
  {
    label: "JOIN",
    sql: `SELECT a.nombre, b.v
FROM alumnos AS a
JOIN t_heap AS b ON a.id = b.id;`,
  },
  {
    label: "CREATE INDEX",
    sql: "CREATE INDEX idx_nota ON alumnos USING BTREE (nota);",
  },
  {
    label: "Transacción",
    sql: `BEGIN TRANSACTION;
UPDATE alumnos SET nota = 20 WHERE id = 1;
ROLLBACK;`,
  },
];
