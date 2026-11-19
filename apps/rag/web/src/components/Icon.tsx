/**
 * Los íconos de la app: trazos propios de 1,5 px sobre una grilla de 20, en `currentColor`.
 * Siempre acompañan a un texto o a un `aria-label`; por sí solos son decoración.
 */
const PATHS = {
  send: "M3.5 10h11M10.5 5.5 15 10l-4.5 4.5",
  stop: "M6 6h8v8H6z",
  close: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  previous: "M12.5 5 7.5 10l5 5",
  next: "M7.5 5l5 5-5 5",
  external: "M8.5 4.5h-4v11h11v-4M11.5 4.5h4v4M15.5 4.5 9 11",
  retry: "M15.5 10a5.5 5.5 0 1 1-1.6-3.9M15.5 4v3.5H12",
  page: "M5.5 3.5h6l3 3v10h-9zM11.5 3.5v3h3",
  layers: "M10 3.5 16.5 7 10 10.5 3.5 7zM3.5 10.5 10 14l6.5-3.5M3.5 13.5 10 17l6.5-3.5",
  gauge: "M4 14.5a6.5 6.5 0 1 1 12 0M10 14.5l3-4.5",
  invert: "M10 3.5a6.5 6.5 0 1 0 0 13zM10 3.5a6.5 6.5 0 1 1 0 13",
  filter: "M3.5 5.5h13M6 10h8M8.5 14.5h3",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { readonly name: IconName; readonly size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
