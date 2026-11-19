import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { DEFAULT_LOCATION } from "../config";
import type { LocationRequest, LocationSource, Point } from "./point";

/** Cuánto se espera al navegador y qué tan vieja puede ser la posición que dé. */
const GEOLOCATION_TIMEOUT_MS = 10_000;
const GEOLOCATION_MAX_AGE_MS = 300_000;

interface LocationState {
  readonly point: Point;
  readonly source: LocationSource;
  readonly request: LocationRequest;
  readonly askBrowser: () => void;
  readonly choosePoint: (point: Point) => void;
  readonly reset: () => void;
}

const LocationContext = createContext<LocationState | null>(null);

/** Redondea a ~10 m: la misma ubicación da las mismas claves de caché. */
function rounded(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

/** Dónde está quien compra: el centro de Lima hasta que el navegador o el mapa digan otra cosa. */
export function LocationProvider({ children }: { readonly children: ReactNode }) {
  const [point, setPoint] = useState<Point>(DEFAULT_LOCATION);
  const [source, setSource] = useState<LocationSource>("default");
  const [request, setRequest] = useState<LocationRequest>("idle");

  const choosePoint = useCallback((next: Point) => {
    setPoint({ lat: rounded(next.lat), lon: rounded(next.lon) });
    setSource("map");
  }, []);

  const askBrowser = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setRequest("unavailable");
      return;
    }
    setRequest("asking");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setPoint({ lat: rounded(position.coords.latitude), lon: rounded(position.coords.longitude) });
        setSource("browser");
        setRequest("idle");
      },
      (error) => setRequest(error.code === error.PERMISSION_DENIED ? "denied" : "unavailable"),
      { timeout: GEOLOCATION_TIMEOUT_MS, maximumAge: GEOLOCATION_MAX_AGE_MS },
    );
  }, []);

  const reset = useCallback(() => {
    setPoint(DEFAULT_LOCATION);
    setSource("default");
    setRequest("idle");
  }, []);

  const value = useMemo(
    () => ({ point, source, request, askBrowser, choosePoint, reset }),
    [point, source, request, askBrowser, choosePoint, reset],
  );
  return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>;
}

export function useShopperLocation(): LocationState {
  const state = useContext(LocationContext);
  if (state === null) {
    throw new Error("useShopperLocation necesita un LocationProvider");
  }
  return state;
}
