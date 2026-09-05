import type { TableInfo } from "../types";

export interface Relation {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
}

const ID_SUFFIX = "_id";

/**
 * Raíces posibles de un nombre de tabla en plural. Se prueban todas porque en castellano
 * el plural se deshace de dos formas: «clientes» → «cliente» (quitar la s) y «ciudades» →
 * «ciudad» (quitar el es). Quedarse con una sola regla deja fuera la mitad de los casos.
 */
function stemsOf(name: string): string[] {
  const stems = [name];
  if (name.endsWith("s")) stems.push(name.slice(0, -1));
  if (name.endsWith("es")) stems.push(name.slice(0, -2));
  return stems;
}

function referencesTable(column: string, table: TableInfo): boolean {
  const target = column.toLowerCase();
  const name = table.name.toLowerCase();
  if (table.primary_key !== null && target === `${name}_${table.primary_key.toLowerCase()}`) {
    return true;
  }
  return stemsOf(name).some((stem) => target === `${stem}${ID_SUFFIX}`);
}

/**
 * Deduce relaciones por convención de nombres: una columna `curso_id` apunta a la clave
 * primaria de `cursos`. El motor todavía no tiene claves foráneas declaradas, así que esto
 * es una lectura del esquema, no una restricción que la base de datos imponga.
 */
export function inferRelations(tables: TableInfo[]): Relation[] {
  const relations: Relation[] = [];
  for (const source of tables) {
    for (const column of source.columns) {
      if (column.primary_key) continue;
      for (const target of tables) {
        if (target.name === source.name || target.primary_key === null) continue;
        if (!referencesTable(column.name, target)) continue;
        const key = target.columns.find((item) => item.name === target.primary_key);
        if (key === undefined || key.type !== column.type) continue;
        relations.push({
          fromTable: source.name,
          fromColumn: column.name,
          toTable: target.name,
          toColumn: target.primary_key,
        });
      }
    }
  }
  return relations;
}

/**
 * Reparte las tablas en columnas por dependencia: las referenciadas a la izquierda y las
 * que referencian a la derecha. Así las flechas van todas en la misma dirección en vez de
 * cruzarse por encima de las tarjetas.
 */
export function layoutInLayers(tables: TableInfo[], relations: Relation[]): TableInfo[][] {
  const byName = new Map(tables.map((table) => [table.name, table]));
  const targets = new Map<string, string[]>();
  for (const relation of relations) {
    const current = targets.get(relation.fromTable) ?? [];
    current.push(relation.toTable);
    targets.set(relation.fromTable, current);
  }

  const depth = new Map<string, number>();
  const resolve = (name: string, seen: Set<string>): number => {
    const cached = depth.get(name);
    if (cached !== undefined) return cached;
    if (seen.has(name)) return 0;
    seen.add(name);
    const referenced = targets.get(name) ?? [];
    const value = referenced.reduce(
      (level, target) => (byName.has(target) ? Math.max(level, resolve(target, seen) + 1) : level),
      0,
    );
    depth.set(name, value);
    return value;
  };

  const columns: TableInfo[][] = [];
  for (const table of tables) {
    const level = resolve(table.name, new Set());
    while (columns.length <= level) {
      columns.push([]);
    }
    columns[level].push(table);
  }
  return columns.filter((column) => column.length > 0);
}
