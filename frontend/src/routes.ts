/**
 * Las páginas de la aplicación y su dirección. El router es la parte de la URL tras `#`:
 * no necesita configurar el servidor (cualquier ruta sirve el mismo `index.html`) ni una
 * dependencia, y los botones «atrás» y «adelante» del navegador funcionan solos.
 */

export type PageId = "consola" | "texto";

export interface Page {
  id: PageId;
  label: string;
  hash: string;
}

export const PAGES: readonly Page[] = [
  { id: "consola", label: "Consola SQL", hash: "#/" },
  { id: "texto", label: "Texto", hash: "#/texto" },
];

const HOME = PAGES[0];

/** La página de un `location.hash`; uno vacío o desconocido lleva a la consola. */
export function pageFromHash(hash: string): PageId {
  return PAGES.find((page) => page.hash === hash)?.id ?? HOME.id;
}
