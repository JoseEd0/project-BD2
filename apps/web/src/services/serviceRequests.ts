/**
 * Expresiones que reconocen una petición a un servicio, para que el service worker las
 * deje pasar a la red sin guardarlas. Son `RegExp` y no funciones porque Workbox copia la
 * configuración dentro de `sw.js`, donde una función perdería lo que importa.
 */

function alternatives(prefixes: readonly string[]): string {
  return prefixes.map((prefix) => prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
}

/** Para la lista de exclusión de navegación de Workbox, que compara ruta y consulta. */
export function servicePathPattern(prefixes: readonly string[]): RegExp {
  return new RegExp(`^(?:${alternatives(prefixes)})(?:[/?]|$)`);
}

/**
 * Para las rutas de Workbox, que comparan la URL completa y, si es de otro origen, solo
 * aceptan una coincidencia desde el primer carácter.
 */
export function serviceUrlPattern(prefixes: readonly string[]): RegExp {
  return new RegExp(`^[a-z][a-z0-9+.-]*://[^/]+(?:${alternatives(prefixes)})(?:[/?]|$)`, "i");
}
