/**
 * El guion de la página «Texto». Vive aparte de `snippets.ts` para que la página no arrastre
 * los atajos de la consola a su trozo del bundle.
 */

/** Búsquedas de ejemplo de la página «Texto»: temas de la muestra ESCI con candidatos de
 * sobra, donde los dos rankings no coinciden, y una booleana, que resalta solo sus términos
 * positivos. */
export const TEXT_SEARCH_EXAMPLES = [
  "auriculares inalámbricos bluetooth",
  "zapatillas running mujer",
  "cafetera italiana inducción",
  "mochila portátil impermeable",
  "funda iphone libro",
  "aceite de oliva virgen extra",
  "funda AND iphone AND NOT silicona",
];

/** Carga el catálogo de la muestra ESCI con su índice invertido, desde la página «Texto»
 * cuando ninguna tabla tiene uno. Lee de `demos/samples/`, que tiene que estar en
 * `MINIGESTOR_IMPORT_DIRS` (ver `.env.example`). */
export const DEMO_CATALOG_SQL = `CREATE TABLE catalogo FROM FILE 'demos/samples/esci/productos.csv'
  USING INDEX INVERTED("texto");`;
