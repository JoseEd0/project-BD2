const PRICE = new Intl.NumberFormat("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const RATING = new Intl.NumberFormat("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const COUNT = new Intl.NumberFormat("es-PE");
const MILLISECONDS = new Intl.NumberFormat("es-PE", { maximumFractionDigits: 1 });
const KILOMETERS = new Intl.NumberFormat("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const METERS_PER_KILOMETER = 1000;
const METERS_ROUNDING = 10;

/** `US$ 12,99`, o `null` si el producto no trae precio. */
export function formatPrice(price: number | null | undefined): string | null {
  return price === null || price === undefined ? null : `US$ ${PRICE.format(price)}`;
}

export function formatRating(rating: number | null | undefined, count: number | null | undefined): string | null {
  if (rating === null || rating === undefined) {
    return null;
  }
  const votes = count === null || count === undefined ? "" : ` (${COUNT.format(count)})`;
  return `${RATING.format(rating)} de 5${votes}`;
}

export function formatCount(value: number): string {
  return COUNT.format(value);
}

export function formatMilliseconds(value: number): string {
  return `${MILLISECONDS.format(value)} ms`;
}

/** Como las frases del API: `850 m` por debajo de un kilómetro, `1,2 km` desde ahí. */
export function formatDistance(meters: number): string {
  const rounded = Math.round(meters / METERS_ROUNDING) * METERS_ROUNDING;
  if (rounded < METERS_PER_KILOMETER) {
    return `${Math.max(rounded, METERS_ROUNDING)} m`;
  }
  return `${KILOMETERS.format(meters / METERS_PER_KILOMETER)} km`;
}

/** Un id de Amazon, largo y opaco, acortado para leerse: `AG3S4F…UQLJQ`. */
export function shortId(id: string, keep = 6): string {
  return id.length <= keep * 2 + 1 ? id : `${id.slice(0, keep)}…${id.slice(-keep + 1)}`;
}
