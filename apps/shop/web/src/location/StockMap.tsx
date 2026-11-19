import "leaflet/dist/leaflet.css";

import L from "leaflet";
import { useEffect, useRef } from "react";

import type { StoreWithStock } from "../api/types";
import { MAP_ATTRIBUTION, MAP_TILES_URL, NEARBY_RADIUS_METERS } from "../config";
import type { Point } from "./point";

const MAX_ZOOM = 19;
const DEFAULT_ZOOM = 12;
const FIT_PADDING: L.PointTuple = [28, 28];
const MARKER_SIZE: L.PointTuple = [30, 30];
const YOU_SIZE: L.PointTuple = [20, 20];

interface StockMapProps {
  readonly point: Point;
  readonly stores: readonly StoreWithStock[];
  readonly selectedStore: number | null;
  readonly choosing: boolean;
  readonly onChoose: (point: Point) => void;
  readonly onSelectStore: (store: number) => void;
}

function storeIcon(index: number, selected: boolean): L.DivIcon {
  return L.divIcon({
    className: selected ? "map-store map-store-selected" : "map-store",
    html: `<span>${index + 1}</span>`,
    iconSize: MARKER_SIZE,
  });
}

/**
 * Las tiendas con stock sobre el mapa de Lima, numeradas como en la lista de al lado, el
 * punto de quien compra y el radio de «en stock cerca». Con `choosing`, un clic elige el punto.
 */
export default function StockMap({ point, stores, selectedStore, choosing, onChoose, onSelectStore }: StockMapProps) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layers = useRef<L.LayerGroup | null>(null);
  const fittedFor = useRef<string | null>(null);
  const handlers = useRef({ onChoose, onSelectStore, choosing });
  handlers.current = { onChoose, onSelectStore, choosing };

  useEffect(() => {
    const element = host.current;
    if (element === null) {
      return;
    }
    const instance = L.map(element, { zoomControl: true, attributionControl: true }).setView(
      [point.lat, point.lon],
      DEFAULT_ZOOM,
    );
    L.tileLayer(MAP_TILES_URL, { attribution: MAP_ATTRIBUTION, maxZoom: MAX_ZOOM }).addTo(instance);
    instance.on("click", (event: L.LeafletMouseEvent) => {
      if (handlers.current.choosing) {
        handlers.current.onChoose({ lat: event.latlng.lat, lon: event.latlng.lng });
      }
    });
    layers.current = L.layerGroup().addTo(instance);
    map.current = instance;
    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      map.current = null;
      layers.current = null;
      instance.remove();
    };
    // El mapa se crea una vez; el punto y las tiendas los pinta el efecto de abajo.
  }, []);

  useEffect(() => {
    const instance = map.current;
    const group = layers.current;
    if (instance === null || group === null) {
      return;
    }
    group.clearLayers();
    const here: L.LatLngTuple = [point.lat, point.lon];
    L.circle(here, { radius: NEARBY_RADIUS_METERS, className: "map-radius", interactive: false }).addTo(group);
    L.marker(here, {
      icon: L.divIcon({ className: "map-you", iconSize: YOU_SIZE }),
      keyboard: false,
      interactive: false,
    }).addTo(group);
    stores.forEach((store, index) => {
      L.marker([store.ubicacion.lat, store.ubicacion.lon], {
        icon: storeIcon(index, store.id === selectedStore),
        title: store.nombre,
        keyboard: false,
        zIndexOffset: store.id === selectedStore ? 1000 : 0,
      })
        .on("click", () => handlers.current.onSelectStore(store.id))
        .addTo(group);
    });
    const view = `${point.lat},${point.lon}:${stores.map((store) => store.id).join(",")}`;
    if (fittedFor.current !== view) {
      fittedFor.current = view;
      const corners = stores.map((store): L.LatLngTuple => [store.ubicacion.lat, store.ubicacion.lon]);
      instance.fitBounds(L.latLngBounds([here, ...corners]), { padding: FIT_PADDING, maxZoom: DEFAULT_ZOOM + 2 });
    }
  }, [point, stores, selectedStore]);

  useEffect(() => {
    host.current?.classList.toggle("map-choosing", choosing);
  }, [choosing]);

  return <div ref={host} className="map" aria-hidden="true" />;
}
