import L from "leaflet";
import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import { MAP_ATTRIBUTION, MAP_TILES_URL } from "../config";

/** Vista inicial, antes de conocer ningún punto: el mundo entero. */
const WORLD_CENTER: L.LatLngTuple = [0, 0];
const WORLD_ZOOM = 2;
const MAX_ZOOM = 19;
const SURFACE_CLASS = "map__surface";

/**
 * Mapa de Leaflet montado dentro del contenedor, con sus teselas. Se dibuja sobre canvas
 * porque el fondo puede traer miles de puntos.
 *
 * Devuelve una referencia y no un estado a propósito: un efecto declarado después de este
 * hook se ejecuta después de él, así que siempre encuentra en la referencia el mapa vivo.
 * Con un estado, un render intermedio podría entregar un mapa que ya fue destruido.
 */
export function useLeafletMap(container: RefObject<HTMLDivElement>): RefObject<L.Map | null> {
  const map = useRef<L.Map | null>(null);

  useEffect(() => {
    const host = container.current;
    if (host === null) {
      return;
    }
    // Cada mapa vive en su propio elemento: al desmontar, el elemento sale de la página
    // en el acto aunque la destrucción del mapa se aplace.
    const surface = document.createElement("div");
    surface.className = SURFACE_CLASS;
    host.append(surface);
    const instance = L.map(surface, { preferCanvas: true }).setView(WORLD_CENTER, WORLD_ZOOM);
    L.tileLayer(MAP_TILES_URL, { attribution: MAP_ATTRIBUTION, maxZoom: MAX_ZOOM }).addTo(instance);
    // El panel cambia de tamaño al arrastrar los divisores, y Leaflet no se entera solo.
    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(surface);
    map.current = instance;
    return () => {
      observer.disconnect();
      map.current = null;
      surface.remove();
      // El lienzo de Leaflet pide sus repintados para el fotograma siguiente y, tras un
      // cambio de vista, olvida cancelarlos al destruirse: se le deja terminar primero.
      requestAnimationFrame(() => instance.remove());
    };
  }, [container]);

  return map;
}
