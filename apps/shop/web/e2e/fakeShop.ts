import type { BrowserContext, Route } from "@playwright/test";

export const CUSTOMER = "AG3S4FROO422V5KP7DJCBXVUQLJQ";
export const GUITAR = "B000GUITAR";
export const CABLE = "B000CABLES";
export const STRINGS = "B000STRING";
export const AMP = "B0000AMPLI";
const LIMA = { lat: -12.0464, lon: -77.0428 };
// Un píxel PNG: basta para que las fotos y las teselas lleguen sin red.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==",
  "base64",
);

function summary(id: string, title: string, price: number | null) {
  return {
    id,
    titulo: title,
    marca: "Fender",
    departamento: "Guitars",
    categoria: "Electric Guitars",
    ruta_categoria: "Musical Instruments > Guitars > Electric Guitars",
    precio: price,
    valoracion: 4.6,
    valoraciones: 120,
    imagen: `/app/data/datasets/amazon_musical_instruments/images/ab/${id}.jpg`,
  };
}

const PRODUCTS = {
  [GUITAR]: summary(GUITAR, "Electric Guitar with Maple Neck", 699.99),
  [CABLE]: summary(CABLE, "Instrument Cable 10 ft", 12.5),
  [STRINGS]: summary(STRINGS, "Acoustic Guitar Strings", 9.99),
  [AMP]: summary(AMP, "Practice Amplifier 20 W", 119),
};

function recommendation(id: keyof typeof PRODUCTS, reasons: [string, number, string][]) {
  const motivos = reasons.map(([senal, aporte, texto]) => ({ senal, aporte, texto }));
  return { producto: PRODUCTS[id], puntaje: 0.5, motivos, explicacion: motivos.map((reason) => reason.texto).join(" · ") };
}

const SIGNAL_SQL = {
  senal: "compras",
  sql: "SELECT otro, veces FROM shop_compras_conjuntas WHERE producto = $1 AND particion = $2 ORDER BY veces DESC, otro LIMIT $3",
  parametros: [GUITAR, "train", 74],
  candidatos: 1,
  milisegundos: 4.5,
};

function recommendations(items: ReturnType<typeof recommendation>[], coldStart = false) {
  return {
    origen: GUITAR,
    arranque_en_frio: coldStart,
    pesos: { compras: 0.4, visual: 0.25, texto: 0.25, cercania: 0.1 },
    version_pesos: "v1",
    senales: [SIGNAL_SQL],
    recomendaciones: items,
  };
}

export interface FakeShop {
  readonly purchases: string[];
}

async function json(route: Route, body: unknown, headers: Record<string, string> = {}, status = 200) {
  await route.fulfill({ status, json: body, headers });
}

/** El API de la tienda simulado, con su estado: las compras cambian lo que se recomienda. */
export async function fakeShop(context: BrowserContext): Promise<FakeShop> {
  const state: FakeShop = { purchases: [] };
  await context.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({ body: PIXEL, contentType: "image/png" }));
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    if (path === "/health") {
      return json(route, { status: "ok", version: "0.1.0" });
    }
    if (path === "/usuarios") {
      return json(route, [{ id: CUSTOMER, compras: 288 }]);
    }
    if (path === "/departamentos") {
      return json(route, [{ departamento: "Guitars", productos: 1066 }]);
    }
    if (path === "/busqueda/texto") {
      return json(route, [{ producto: PRODUCTS[GUITAR], puntaje: 5.2 }], { "Server-Timing": "gestor;dur=6, total;dur=7" });
    }
    if (path.endsWith("/foto")) {
      return route.fulfill({ body: PIXEL, contentType: "image/png" });
    }
    if (path === `/productos/${GUITAR}`) {
      return json(route, { ...PRODUCTS[GUITAR], descripcion: "Maple neck | Two humbuckers\n\nA solid body guitar." });
    }
    if (path === `/productos/${GUITAR}/tiendas`) {
      return json(route, [{ id: 101, nombre: "Centro", tipo: "tienda", ubicacion: LIMA, metros: 180, unidades: 2 }]);
    }
    if (path === `/productos/${GUITAR}/recomendaciones`) {
      return json(
        route,
        recommendations([recommendation(CABLE, [["compras", 0.4, "comprado junto con este producto por 12 clientes"]])]),
        { "X-Cache": "hit", "Server-Timing": "cache;dur=1.2, total;dur=2" },
      );
    }
    if (path === `/usuarios/${CUSTOMER}/recomendaciones`) {
      const bought = state.purchases.length > 0;
      const items = bought
        ? [
            recommendation(AMP, [["compras", 0.4, "comprado junto con 1 de tus compras por 9 clientes"]]),
            recommendation(STRINGS, [["texto", 0.25, "descripción parecida a tus compras"]]),
          ]
        : [recommendation(STRINGS, [["texto", 0.25, "descripción parecida a tus compras"]])];
      return json(route, recommendations(items), { "X-Cache": bought ? "miss" : "hit", "Server-Timing": "total;dur=3" });
    }
    if (path === `/usuarios/${CUSTOMER}/compras` && request.method() === "POST") {
      const product = (request.postDataJSON() as { producto: string }).producto;
      state.purchases.push(product);
      return json(
        route,
        { id: 511837, usuario: CUSTOMER, producto: product, instante: 1, particion: "train", pares_sumados: 3, pares_nuevos: 2 },
        {},
        201,
      );
    }
    if (path === `/usuarios/${CUSTOMER}/compras`) {
      return json(route, state.purchases.map((id) => PRODUCTS[id as keyof typeof PRODUCTS]));
    }
    return json(route, { error: "no existe", kind: "ResourceNotFoundError" }, {}, 404);
  });
  return state;
}
