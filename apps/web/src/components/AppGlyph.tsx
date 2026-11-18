import type { ServiceId } from "../services/catalog";

const GLYPH_PATHS: Record<ServiceId, readonly string[]> = {
  copyright: ["M4 13v-2", "M8 16V8", "M12 19V5", "M16 15V9", "M20 13v-2"],
  shop: ["M5 8h14l-1.2 11.2a1 1 0 0 1-1 .8H7.2a1 1 0 0 1-1-.8Z", "M9 8V6.5a3 3 0 0 1 6 0V8", "M9.5 12.5a2.5 2.5 0 0 0 5 0"],
  papers: ["M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z", "M14 3v4h4", "M9 12h6", "M9 15.5h6", "M9 19h3"],
  faces: [
    "M4 8V5a1 1 0 0 1 1-1h3",
    "M16 4h3a1 1 0 0 1 1 1v3",
    "M20 16v3a1 1 0 0 1-1 1h-3",
    "M8 20H5a1 1 0 0 1-1-1v-3",
    "M9 10v.5",
    "M15 10v.5",
    "M9.5 15a3.5 3.5 0 0 0 5 0",
  ],
  engine: [
    "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Z",
    "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6",
    "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  ],
};

/** El símbolo de cada servicio, en el color de su aplicación (`currentColor`). */
export function AppGlyph({ service }: { readonly service: ServiceId }) {
  return (
    <svg className="glyph" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {GLYPH_PATHS[service].map((path) => (
        <path key={path} d={path} />
      ))}
    </svg>
  );
}
