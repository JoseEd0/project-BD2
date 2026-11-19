import type { ProductSummary, Recommendation, Recommendations } from "../src/api/types";

export function product(id: string, overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    id,
    titulo: `Product ${id}`,
    marca: "Fender",
    departamento: "Guitars",
    categoria: "Electric Guitars",
    ruta_categoria: "Musical Instruments > Guitars > Electric Guitars",
    precio: 99.5,
    valoracion: 4.5,
    valoraciones: 120,
    imagen: `/data/images/${id}.jpg`,
    ...overrides,
  };
}

function recommendation(id: string, reasons: [string, number, string][]): Recommendation {
  const motivos = reasons.map(([senal, aporte, texto]) => ({ senal, aporte, texto }));
  return {
    producto: product(id),
    puntaje: motivos.reduce((sum, reason) => sum + reason.aporte, 0),
    motivos,
    explicacion: motivos.map((reason) => reason.texto).join(" · "),
  };
}

export const CABLE = "B000CABLES";
export const STRINGS = "B000STRING";
export const PEDAL = "B000PEDALS";

/** Tres recomendaciones: el cable aporta más por compras, las cuerdas solo por texto y cercanía. */
export function recommendations(overrides: Partial<Recommendations> = {}): Recommendations {
  return {
    origen: "B000GUITAR",
    arranque_en_frio: false,
    pesos: { compras: 0.4, visual: 0.25, texto: 0.25, cercania: 0.1 },
    version_pesos: "v1",
    senales: [
      {
        senal: "compras",
        sql: "SELECT otro, veces FROM shop_compras_conjuntas WHERE producto = $1 AND particion = $2 ORDER BY veces DESC, otro LIMIT $3",
        parametros: ["B000GUITAR", "train", 50],
        candidatos: 2,
        milisegundos: 4.5,
      },
      {
        senal: "texto",
        sql: "SELECT id, SCORE() AS puntaje FROM shop_productos WHERE MATCH(texto, $1) USING BM25 ORDER BY puntaje DESC LIMIT $2",
        parametros: ["electric guitar", 50],
        candidatos: 9,
        milisegundos: 23.6,
      },
      {
        senal: "cercania",
        sql: "SELECT i.producto AS producto FROM shop_inventario i JOIN shop_tiendas t ON i.tienda = t.id WHERE distancia(t.ubicacion, $1) < $2",
        parametros: [{ lat: -12.05, lon: -77.04 }, 5000, CABLE],
        candidatos: 2,
        milisegundos: 31.2,
      },
    ],
    recomendaciones: [
      recommendation(CABLE, [
        ["compras", 0.4, "comprado junto con este producto por 12 clientes"],
        ["cercania", 0.08, "en stock a 1,2 km en Centro"],
      ]),
      recommendation(PEDAL, [["compras", 0.1, "comprado junto con este producto por 3 clientes"]]),
      recommendation(STRINGS, [
        ["texto", 0.25, "descripción parecida"],
        ["cercania", 0.09, "en stock a 400 m en Miraflores"],
      ]),
    ],
    ...overrides,
  };
}
