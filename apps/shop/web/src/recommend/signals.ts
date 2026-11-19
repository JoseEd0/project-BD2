/** Las cuatro señales del recomendador, con el nombre que les da el API (`senal`). */
export type Signal = "compras" | "visual" | "texto" | "cercania";

export const SIGNALS: readonly Signal[] = ["compras", "visual", "texto", "cercania"];

/** De qué parte la recomendación: un producto o las compras de un cliente. */
export type Scope = "producto" | "usuario";

interface SignalWording {
  /** Lo que dice el sello: corto, en mayúsculas al pintarse. */
  readonly stamp: string;
  readonly title: Record<Scope, string>;
  /** Qué clase de datos del gestor la responde. */
  readonly source: string;
  /** Por qué una fila de esta señal puede quedar vacía. */
  readonly whenEmpty: Record<Scope, string>;
}

export const SIGNAL_WORDING: Readonly<Record<Signal, SignalWording>> = {
  compras: {
    stamp: "Comprado junto",
    title: { producto: "Comprado junto con este producto", usuario: "Comprado junto con lo que compraste" },
    source: "relacional · B+ sobre los pares materializados",
    whenEmpty: {
      producto: "Ningún par de clientes compró este producto junto con otro al menos dos veces (arranque en frío).",
      usuario: "Tus compras no tienen pares de compras conjuntas todavía.",
    },
  },
  visual: {
    stamp: "Se ve parecido",
    title: { producto: "Se ve parecido", usuario: "Se parece a lo que compraste" },
    source: "vectorial · HNSW de CLIP",
    whenEmpty: {
      producto: "Este producto no tiene foto, así que no hay con qué comparar.",
      usuario: "Ninguna de tus compras tiene foto.",
    },
  },
  texto: {
    stamp: "Descripción",
    title: { producto: "Descripción parecida", usuario: "Descripción parecida a tus compras" },
    source: "texto · BM25 sobre el invertido en inglés",
    whenEmpty: {
      producto: "Ningún otro producto comparte palabras con este título.",
      usuario: "Tus compras no tienen título con el que buscar.",
    },
  },
  cercania: {
    stamp: "En stock cerca",
    title: { producto: "En stock cerca de ti", usuario: "En stock cerca de ti" },
    source: "espacial · hash del inventario y R-Tree",
    whenEmpty: {
      producto: "Ninguna tienda en el radio tiene stock de lo que proponen las otras señales.",
      usuario: "Ninguna tienda en el radio tiene stock de lo que proponen las otras señales.",
    },
  },
};

export function toSignal(value: string): Signal | null {
  return SIGNALS.find((signal) => signal === value) ?? null;
}
