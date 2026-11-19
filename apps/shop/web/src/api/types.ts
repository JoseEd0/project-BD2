import type { components } from "./generated/shop";

type Schemas = components["schemas"];

export type ProductSummary = Schemas["ProductSummary"];
export type Product = Schemas["Product"];
export type ScoredProduct = Schemas["ScoredProduct"];
export type Recommendations = Schemas["Recommendations"];
export type Recommendation = Schemas["Recommendation"];
export type Reason = Schemas["Reason"];
export type SignalQuery = Schemas["SignalQuery"];
export type StoreWithStock = Schemas["StoreWithStock"];
export type Department = Schemas["Department"];
export type Customer = Schemas["Customer"];
export type PurchaseRecorded = Schemas["PurchaseRecorded"];

/** Lo que dice la cabecera `X-Cache` del API: `hit`, `miss` o `bypass` (sin Redis). */
export type CacheStatus = "hit" | "miss" | "bypass";

/** Una respuesta con lo que traen sus cabeceras: el reparto del tiempo y la caché. */
export interface Measured<T> {
  readonly data: T;
  readonly serverTiming: string | null;
  readonly cache: CacheStatus | null;
}
