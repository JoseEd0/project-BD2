/** Lo que filtra una búsqueda: el departamento, el rango de precio en dólares y la valoración mínima. */
export interface CatalogFilters {
  readonly department: string | null;
  readonly minPrice: number | null;
  readonly maxPrice: number | null;
  readonly minRating: number | null;
}

export const NO_FILTERS: CatalogFilters = { department: null, minPrice: null, maxPrice: null, minRating: null };

/** Las valoraciones de un clic, como en cualquier tienda: «4 estrellas o más». */
export const RATING_STEPS: readonly number[] = [4, 3, 2];

export interface PriceRange {
  readonly label: string;
  readonly min: number | null;
  readonly max: number | null;
}

/** Tramos de precio de un clic; cubren la mediana del catálogo (US$ 31) y su cola. */
export const PRICE_RANGES: readonly PriceRange[] = [
  { label: "Hasta US$ 25", min: null, max: 25 },
  { label: "US$ 25 a 100", min: 25, max: 100 },
  { label: "US$ 100 a 500", min: 100, max: 500 },
  { label: "Más de US$ 500", min: 500, max: null },
];

/** Los nombres de los parámetros en la URL del catálogo. */
export const PARAM = {
  query: "q",
  mode: "modo",
  department: "departamento",
  minPrice: "min",
  maxPrice: "max",
  minRating: "estrellas",
} as const;

function price(value: string | null): number | null {
  if (value === null || value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function filtersFromParams(params: URLSearchParams): CatalogFilters {
  const department = params.get(PARAM.department);
  return {
    department: department === null || department === "" ? null : department,
    minPrice: price(params.get(PARAM.minPrice)),
    maxPrice: price(params.get(PARAM.maxPrice)),
    minRating: price(params.get(PARAM.minRating)),
  };
}

/** Los mismos parámetros con `filters` en lugar de los que había; los demás quedan. */
export function withFilters(params: URLSearchParams, filters: CatalogFilters): URLSearchParams {
  const next = new URLSearchParams(params);
  const entries: [string, string | number | null][] = [
    [PARAM.department, filters.department],
    [PARAM.minPrice, filters.minPrice],
    [PARAM.maxPrice, filters.maxPrice],
    [PARAM.minRating, filters.minRating],
  ];
  for (const [name, value] of entries) {
    if (value === null) {
      next.delete(name);
    } else {
      next.set(name, String(value));
    }
  }
  return next;
}

export function activeFilterCount(filters: CatalogFilters): number {
  const price = filters.minPrice !== null || filters.maxPrice !== null ? 1 : 0;
  return (filters.department === null ? 0 : 1) + price + (filters.minRating === null ? 0 : 1);
}

export function isPriceRange(filters: CatalogFilters, range: PriceRange): boolean {
  return filters.minPrice === range.min && filters.maxPrice === range.max;
}

/** Un precio fuera de orden se avisa antes de preguntar al API. */
export function priceRangeIsUpsideDown(filters: CatalogFilters): boolean {
  return filters.minPrice !== null && filters.maxPrice !== null && filters.minPrice > filters.maxPrice;
}

/** El filtro como parámetros de consulta del API. */
export function filterQuery(filters: CatalogFilters): {
  departamento?: string;
  precio_min?: number;
  precio_max?: number;
  valoracion_min?: number;
} {
  return {
    ...(filters.department === null ? {} : { departamento: filters.department }),
    ...(filters.minPrice === null ? {} : { precio_min: filters.minPrice }),
    ...(filters.maxPrice === null ? {} : { precio_max: filters.maxPrice }),
    ...(filters.minRating === null ? {} : { valoracion_min: filters.minRating }),
  };
}
