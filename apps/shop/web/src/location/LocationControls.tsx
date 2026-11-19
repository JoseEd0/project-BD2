import { Icon } from "../components/Icon";
import { useShopperLocation } from "./LocationContext";
import type { LocationRequest, LocationSource } from "./point";

const SOURCE_TEXT: Record<LocationSource, string> = {
  default: "Desde el centro de Lima (Plaza Mayor), por omisión.",
  browser: "Desde tu ubicación.",
  map: "Desde el punto que elegiste en el mapa.",
};

const REQUEST_TEXT: Partial<Record<LocationRequest, string>> = {
  asking: "Esperando la ubicación del navegador…",
  denied: "No diste permiso de ubicación: seguimos desde el centro de Lima. Puedes elegir un punto en el mapa.",
  unavailable: "Tu navegador no pudo dar la ubicación: seguimos desde el centro de Lima. Puedes elegir un punto en el mapa.",
};

interface LocationControlsProps {
  readonly choosing: boolean;
  readonly onToggleChoosing: () => void;
}

/** Desde dónde se mide «cerca», y cómo cambiarlo: el navegador, un clic en el mapa o volver a Lima. */
export function LocationControls({ choosing, onToggleChoosing }: LocationControlsProps) {
  const { source, request, askBrowser, reset } = useShopperLocation();
  const problem = REQUEST_TEXT[request];
  return (
    <div className="location">
      <p className="location-source" aria-live="polite">
        <Icon name="pin" size={18} />
        <span>{problem ?? SOURCE_TEXT[source]}</span>
      </p>
      <div className="location-actions">
        <button type="button" className="button button-quiet" onClick={askBrowser} disabled={request === "asking"}>
          <Icon name="locate" size={18} />
          Usar mi ubicación
        </button>
        <button type="button" className="button button-quiet" aria-pressed={choosing} onClick={onToggleChoosing}>
          <Icon name="pin" size={18} />
          {choosing ? "Haz clic en el mapa…" : "Elegir en el mapa"}
        </button>
        {source !== "default" && (
          <button type="button" className="button button-link" onClick={reset}>
            Volver al centro de Lima
          </button>
        )}
      </div>
    </div>
  );
}
