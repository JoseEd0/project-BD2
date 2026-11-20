import { keepPreviousData, useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";

import { NEARBY_RADIUS_METERS, PREVIEW_RESULTS, PURCHASES_SHOWN, RECOMMENDATIONS, SEARCH_RESULTS, STORES_SHOWN } from "../config";
import { filterQuery, NO_FILTERS, type CatalogFilters } from "../catalog/filters";
import type { TextSearchMode } from "../catalog/searchMode";
import type { Point } from "../location/point";
import { shopClient } from "./client";
import type { ApiProblem } from "./problem";
import { body, measured } from "./request";
import type {
  Customer,
  Department,
  Measured,
  Product,
  ProductSummary,
  PurchaseRecorded,
  Recommendations,
  ScoredProduct,
  StoreWithStock,
  TextSearchResult,
} from "./types";

/** Las claves de la caché de React Query: una compra invalida todo lo que cuelga de `recommendations`. */
export const QUERY_KEYS = {
  departments: ["departamentos"] as const,
  customers: ["usuarios"] as const,
  product: (id: string) => ["producto", id] as const,
  search: (mode: TextSearchMode, query: string, filters: CatalogFilters, exact: boolean) =>
    ["busqueda", mode, query, filters, exact] as const,
  preview: (query: string) => ["vista-previa", query] as const,
  stock: (id: string, at: Point) => ["stock", id, at.lat, at.lon] as const,
  recommendations: ["recomendaciones"] as const,
  productRecommendations: (id: string, customer: string | null, at: Point) =>
    ["recomendaciones", "producto", id, customer, at.lat, at.lon] as const,
  customerRecommendations: (customer: string, at: Point) => ["recomendaciones", "usuario", customer, at.lat, at.lon] as const,
  purchases: (customer: string) => ["compras", customer] as const,
};

function nearby(at: Point) {
  return { lat: at.lat, lon: at.lon, radio: NEARBY_RADIUS_METERS };
}

export function useDepartments(): UseQueryResult<Department[], ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.departments,
    queryFn: () => body(() => shopClient.GET("/departamentos")),
    staleTime: Infinity,
  });
}

export function useCustomers(): UseQueryResult<Customer[], ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.customers,
    queryFn: () => body(() => shopClient.GET("/usuarios")),
    staleTime: Infinity,
  });
}

export function useProduct(id: string): UseQueryResult<Product, ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.product(id),
    queryFn: () => body(() => shopClient.GET("/productos/{product_id}", { params: { path: { product_id: id } } })),
    staleTime: Infinity,
  });
}

/** Lo encontrado y, si la búsqueda por palabras corrigió una errata, la consulta que buscó. */
export interface SearchOutcome {
  readonly products: readonly ScoredProduct[];
  readonly correctedQuery: string | null;
}

function outcomeOf(result: TextSearchResult): SearchOutcome {
  return { products: result.resultados, correctedQuery: result.corregida };
}

function forgivingSearch(query: string, limit: number, filters: CatalogFilters, exact: boolean) {
  const params = { query: { q: query, limite: limit, exacta: exact, ...filterQuery(filters) } };
  return shopClient.GET("/busqueda/productos", { params });
}

async function searchByWords(query: string, filters: CatalogFilters, exact: boolean): Promise<Measured<SearchOutcome>> {
  const result = await measured(() => forgivingSearch(query, SEARCH_RESULTS, filters, exact));
  return { ...result, data: outcomeOf(result.data) };
}

async function searchByLooks(query: string, filters: CatalogFilters): Promise<Measured<SearchOutcome>> {
  const params = { query: { q: query, limite: SEARCH_RESULTS, ...filterQuery(filters) } };
  const result = await measured(() => shopClient.GET("/busqueda/imagenes", { params }));
  return { ...result, data: { products: result.data, correctedQuery: null } };
}

/** Por palabras (BM25 que perdona erratas, salvo con `exact`) o por cómo se ve (CLIP). */
export function useTextSearch(
  mode: TextSearchMode,
  query: string,
  filters: CatalogFilters,
  exact: boolean,
): UseQueryResult<Measured<SearchOutcome>, ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.search(mode, query, filters, exact),
    queryFn: () => (mode === "palabras" ? searchByWords(query, filters, exact) : searchByLooks(query, filters)),
    enabled: query.trim() !== "",
    placeholderData: keepPreviousData,
  });
}

/** Los primeros productos para lo que se está escribiendo, bajo el buscador. */
export function useSearchPreview(query: string, enabled: boolean): UseQueryResult<SearchOutcome, ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.preview(query),
    queryFn: async () => outcomeOf(await body(() => forgivingSearch(query, PREVIEW_RESULTS, NO_FILTERS, false))),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

export interface PhotoSearch {
  readonly photo: Blob;
  readonly filters: CatalogFilters;
}

export function usePhotoSearch() {
  return useMutation<Measured<SearchOutcome>, ApiProblem, PhotoSearch>({
    mutationFn: ({ photo, filters }) => {
      const form = new FormData();
      form.append("foto", photo, "foto.jpg");
      return measured(() =>
        shopClient.POST("/busqueda/foto", {
          params: { query: { limite: SEARCH_RESULTS, ...filterQuery(filters) } },
          body: { foto: "" },
          bodySerializer: () => form,
        }),
      ).then((result) => ({ ...result, data: { products: result.data, correctedQuery: null } }));
    },
  });
}

export function useStoresWithStock(id: string, at: Point): UseQueryResult<StoreWithStock[], ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.stock(id, at),
    queryFn: () =>
      body(() =>
        shopClient.GET("/productos/{product_id}/tiendas", {
          params: { path: { product_id: id }, query: { lat: at.lat, lon: at.lon, limite: STORES_SHOWN } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useProductRecommendations(
  id: string,
  customer: string | null,
  at: Point,
): UseQueryResult<Measured<Recommendations>, ApiProblem> {
  const query = { limite: RECOMMENDATIONS, ...nearby(at), ...(customer === null ? {} : { usuario: customer }) };
  return useQuery({
    queryKey: QUERY_KEYS.productRecommendations(id, customer, at),
    queryFn: () =>
      measured(() =>
        shopClient.GET("/productos/{product_id}/recomendaciones", { params: { path: { product_id: id }, query } }),
      ) as Promise<Measured<Recommendations>>,
    placeholderData: keepPreviousData,
  });
}

export function useCustomerRecommendations(
  customer: string | null,
  at: Point,
): UseQueryResult<Measured<Recommendations>, ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.customerRecommendations(customer ?? "", at),
    queryFn: () =>
      measured(() =>
        shopClient.GET("/usuarios/{user_id}/recomendaciones", {
          params: { path: { user_id: customer ?? "" }, query: { limite: RECOMMENDATIONS, ...nearby(at) } },
        }),
      ) as Promise<Measured<Recommendations>>,
    enabled: customer !== null,
    placeholderData: keepPreviousData,
  });
}

export function useCustomerPurchases(customer: string | null): UseQueryResult<ProductSummary[], ApiProblem> {
  return useQuery({
    queryKey: QUERY_KEYS.purchases(customer ?? ""),
    queryFn: () =>
      body(() =>
        shopClient.GET("/usuarios/{user_id}/compras", {
          params: { path: { user_id: customer ?? "" }, query: { limite: PURCHASES_SHOWN } },
        }),
      ),
    enabled: customer !== null,
  });
}

export interface PurchaseOrder {
  readonly customer: string;
  readonly product: string;
}

/** Registra la compra; después, las recomendaciones y las compras del cliente se piden de nuevo. */
export function usePurchase() {
  const queryClient = useQueryClient();
  return useMutation<PurchaseRecorded, ApiProblem, PurchaseOrder>({
    mutationFn: ({ customer, product }) =>
      body(() =>
        shopClient.POST("/usuarios/{user_id}/compras", {
          params: { path: { user_id: customer } },
          body: { producto: product },
        }),
      ),
    onSuccess: async (_, { customer }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.recommendations }),
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.purchases(customer) }),
      ]);
    },
  });
}
