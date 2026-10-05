import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchStructure, fetchTablePoints } from "../api";
import { useLeafletMap } from "../hooks/useLeafletMap";
import type { Theme } from "../hooks/useTheme";
import { formatPoint, isGeoPoint } from "../lib/points";
import { euclideanRing, locatedRows, overlayLabel } from "../lib/spatial";
import type { LocatedRow } from "../lib/spatial";
import type {
  CellValue,
  GeoPoint,
  Overlay,
  QueryResponse,
  RTreeStructure,
  SpatialInfo,
  TableInfo,
  TablePoints,
} from "../types";

interface MapPanelProps {
  result: QueryResponse;
  spatial: SpatialInfo;
  tables: TableInfo[];
  theme: Theme;
}

/** Puntos del resultado que se resaltan como mucho; más allá el mapa deja de ser legible. */
const MAX_HIGHLIGHTED = 5000;
/** Columnas de la fila que caben en la ficha de un punto. */
const POPUP_FIELDS = 6;
const FIT_PADDING: L.PointTuple = [36, 36];
const FIT_MAX_ZOOM = 16;
/** Parte del alto del mapa que el encuadre cede como mucho a la leyenda. */
const MAX_LEGEND_SHARE = 0.5;
const PIN_SIZE: L.PointTuple = [16, 16];
/** El rombo va en un elemento interior: girar el icono mismo desviaría su posición. */
const PIN_SHAPE = '<span class="map-pin__shape"></span>';
const CONTEXT_RADIUS = 2.5;
const RESULT_RADIUS = 5.5;
const SPATIAL_INDEX = "RTREE";

/** Qué se encuadró por última vez y en qué mapa, para no reencuadrar al cambiar de tema. */
interface Framing {
  map: L.Map;
  subject: QueryResponse | TablePoints;
}

interface Palette {
  context: string;
  result: string;
  resultEdge: string;
  figure: string;
  boxes: string;
}

function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement);
  const color = (name: string) => style.getPropertyValue(name).trim();
  return {
    context: color("--text-faint"),
    result: color("--accent"),
    resultEdge: color("--surface-1"),
    figure: color("--key"),
    boxes: color("--teal"),
  };
}

function latLng(point: GeoPoint): L.LatLngTuple {
  return [point.lat, point.lon];
}

function cellLabel(value: CellValue): string {
  if (value === null) return "NULL";
  return isGeoPoint(value) ? formatPoint(value) : String(value);
}

/** Ficha de una fila. Se arma con nodos, no con HTML: los valores vienen de los datos. */
function popupFor(columns: string[], row: CellValue[]): HTMLElement {
  const list = document.createElement("dl");
  list.className = "map-card";
  columns.slice(0, POPUP_FIELDS).forEach((column, index) => {
    const term = document.createElement("dt");
    term.textContent = column;
    const detail = document.createElement("dd");
    detail.textContent = cellLabel(row[index]);
    list.append(term, detail);
  });
  return list;
}

function figureLayers(overlay: Overlay, palette: Palette): L.Layer[] {
  const outline: L.PathOptions = {
    color: palette.figure,
    weight: 2,
    fillOpacity: 0.07,
    interactive: false,
  };
  if (overlay.kind === "polygon") {
    return [L.polygon(overlay.vertices.map(latLng), outline)];
  }
  if (overlay.center === null) {
    return [];
  }
  const pin = L.marker(latLng(overlay.center), {
    icon: L.divIcon({ className: "map-pin", html: PIN_SHAPE, iconSize: PIN_SIZE }),
    keyboard: false,
  }).bindTooltip(`Punto de la consulta ${formatPoint(overlay.center)}`);
  if (overlay.kind === "nearest" || overlay.radius === null) {
    return [pin];
  }
  const area =
    overlay.unit === "m"
      ? L.circle(latLng(overlay.center), { ...outline, radius: overlay.radius })
      : L.polygon(euclideanRing(overlay.center, overlay.radius), outline);
  return [area, pin];
}

function boxLayers(tree: RTreeStructure, palette: Palette): L.Layer[] {
  const leaves = tree.levels[tree.levels.length - 1];
  return leaves.nodes.flatMap((node) =>
    node.bounds === null
      ? []
      : [
          L.rectangle(
            [
              [node.bounds[0], node.bounds[1]],
              [node.bounds[2], node.bounds[3]],
            ],
            { color: palette.boxes, weight: 1, fill: false, interactive: false },
          ),
        ],
  );
}

function contextLayers(context: TablePoints, palette: Palette): L.Layer[] {
  return context.points.map((point) =>
    L.circleMarker(point, {
      radius: CONTEXT_RADIUS,
      stroke: false,
      fillColor: palette.context,
      fillOpacity: 0.55,
      interactive: false,
    }),
  );
}

function resultLayers(located: LocatedRow[], columns: string[], palette: Palette): L.Layer[] {
  return located.map(({ point, row }) =>
    L.circleMarker(latLng(point), {
      radius: RESULT_RADIUS,
      color: palette.resultEdge,
      weight: 1.5,
      fillColor: palette.result,
      fillOpacity: 0.95,
    }).bindPopup(() => popupFor(columns, row)),
  );
}

/** Puntos de la tabla consultada, para que el resultado se vea sobre el total. */
/** Alto que la leyenda tapa desde el borde inferior del mapa: el encuadre lo deja libre. */
function legendClearance(legend: HTMLElement | null): number {
  const frame = legend?.offsetParent;
  if (legend === null || !frame) {
    return 0;
  }
  return Math.min(frame.clientHeight - legend.offsetTop, frame.clientHeight * MAX_LEGEND_SHARE);
}

function useTablePoints(spatial: SpatialInfo, rowCount: number): TablePoints | null {
  const [points, setPoints] = useState<TablePoints | null>(null);
  useEffect(() => {
    let current = true;
    fetchTablePoints(spatial.table, spatial.column)
      .then((loaded) => current && setPoints(loaded))
      .catch(() => current && setPoints(null));
    return () => {
      current = false;
    };
  }, [spatial.table, spatial.column, rowCount]);
  return points;
}

/** Estructura del R-Tree de la columna, solo mientras se pide ver sus MBR. */
function useSpatialTree(spatial: SpatialInfo, wanted: boolean, rowCount: number): RTreeStructure | null {
  const [tree, setTree] = useState<RTreeStructure | null>(null);
  useEffect(() => {
    if (!wanted) {
      setTree(null);
      return;
    }
    let current = true;
    fetchStructure(spatial.table)
      .then((structure) => {
        const index = structure.indexes.find((item) => item.column === spatial.column);
        if (current && index?.structure.kind === "rtree") {
          setTree(index.structure);
        }
      })
      .catch(() => current && setTree(null));
    return () => {
      current = false;
    };
  }, [spatial.table, spatial.column, wanted, rowCount]);
  return tree;
}

export default function MapPanel({ result, spatial, tables, theme }: MapPanelProps) {
  const container = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const map = useLeafletMap(container);
  const framed = useRef<Framing | null>(null);
  const [showBoxes, setShowBoxes] = useState(false);

  const table = tables.find((item) => item.name === spatial.table);
  const rowCount = table?.row_count ?? 0;
  const indexed =
    table?.columns.find((column) => column.name === spatial.column)?.indexed_with === SPATIAL_INDEX;

  const context = useTablePoints(spatial, rowCount);
  const tree = useSpatialTree(spatial, showBoxes && indexed, rowCount);
  const located = useMemo(() => locatedRows(result.rows), [result.rows]);
  const highlighted = useMemo(() => located.slice(0, MAX_HIGHLIGHTED), [located]);

  useEffect(() => {
    const instance = map.current;
    if (instance === null) {
      return;
    }
    const palette = readPalette();
    const backdrop = L.layerGroup([
      ...(tree === null ? [] : boxLayers(tree, palette)),
      ...(context === null ? [] : contextLayers(context, palette)),
    ]).addTo(instance);
    const focus = L.featureGroup([
      ...spatial.overlays.flatMap((overlay) => figureLayers(overlay, palette)),
      ...resultLayers(highlighted, result.columns, palette),
    ]).addTo(instance);

    // El encuadre sigue a la consulta, no al tema ni a las capas de apoyo: cambia cuando
    // llega otro resultado o, si este no trae nada que enfocar, cuando llega el fondo.
    const hasFocus = focus.getLayers().length > 0;
    const subject = hasFocus ? result : context;
    const alreadyFramed = framed.current?.map === instance && framed.current.subject === subject;
    if (subject !== null && !alreadyFramed) {
      const bounds = hasFocus ? focus.getBounds() : L.latLngBounds(context?.points ?? []);
      if (bounds.isValid()) {
        // Sin animación: el encuadre es instantáneo y no deja una transición a medias si
        // la consulta siguiente llega enseguida y el mapa se desmonta.
        instance.fitBounds(bounds, {
          paddingTopLeft: FIT_PADDING,
          paddingBottomRight: [FIT_PADDING[0], FIT_PADDING[1] + legendClearance(legend.current)],
          maxZoom: FIT_MAX_ZOOM,
          animate: false,
        });
        framed.current = { map: instance, subject };
      }
    }
    return () => {
      // Si el mapa ya se está destruyendo, él mismo retira sus capas; quitarlas aquí le
      // pediría un repintado más, justo cuando ya no va a poder hacerlo.
      if (map.current === instance) {
        backdrop.remove();
        focus.remove();
      }
    };
  }, [map, context, tree, highlighted, result, spatial.overlays, theme]);

  const sampled = context !== null && context.points.length < context.total;

  return (
    <div className="map">
      <div aria-label="Mapa de los resultados" className="map__canvas" ref={container} role="img" />
      <div className="map__legend" ref={legend}>
        <p className="map__key">
          <span className="map__dot map__dot--result" />
          {located.length === 0
            ? "Sin filas que situar"
            : `${located.length.toLocaleString("es")} fila(s) del resultado`}
        </p>
        {located.length > highlighted.length && (
          <p className="map__note">
            Se resaltan las primeras {highlighted.length.toLocaleString("es")}.
          </p>
        )}
        {located.length === 0 && result.rows.length > 0 && (
          <p className="map__note">
            Añade <code>{spatial.column}</code> al <code>SELECT</code> para resaltar las filas.
          </p>
        )}
        {spatial.overlays.map((overlay, index) => (
          <p className="map__key" key={index}>
            <span className="map__dot map__dot--figure" />
            {overlayLabel(overlay)}
          </p>
        ))}
        {context !== null && (
          <p className="map__key">
            <span className="map__dot map__dot--context" />
            {sampled
              ? `${spatial.table}: muestra de ${context.points.length.toLocaleString("es")} de ${context.total.toLocaleString("es")} filas`
              : `${spatial.table}: ${context.points.length.toLocaleString("es")} puntos`}
          </p>
        )}
        {indexed && (
          <label className="map__toggle">
            <input
              checked={showBoxes}
              onChange={(event) => setShowBoxes(event.target.checked)}
              type="checkbox"
            />
            <span className="map__dot map__dot--boxes" />
            MBR de las hojas del R-Tree
            {tree !== null && ` (${tree.levels[tree.levels.length - 1].node_count})`}
          </label>
        )}
      </div>
    </div>
  );
}
