/** Un punto en el mapa, como lo pide el API (`lat`, `lon`). */
export interface Point {
  readonly lat: number;
  readonly lon: number;
}

/** De dónde salió la ubicación: el centro de Lima, el navegador o un clic en el mapa. */
export type LocationSource = "default" | "browser" | "map";

/** Qué pasó al pedirle la ubicación al navegador. */
export type LocationRequest = "idle" | "asking" | "denied" | "unavailable";
