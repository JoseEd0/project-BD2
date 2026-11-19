/** Iconos propios: un trazo de 1,75 px sobre una rejilla de 24, del color del texto. */
const PATHS = {
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm4.6-1.9L20 20",
  camera: "M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5v-9ZM12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  photo: "M4.5 5h15a.5.5 0 0 1 .5.5v13a.5.5 0 0 1-.5.5h-15a.5.5 0 0 1-.5-.5v-13a.5.5 0 0 1 .5-.5Zm0 11 4.5-4.5 4 4 2.5-2.5 4.5 4.5M15 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z",
  close: "M6 6l12 12M18 6 6 18",
  alert: "M12 8.5v4.5m0 3.2v.1M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  retry: "M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4v4.5H9",
  left: "M14.5 6 8.5 12l6 6",
  right: "M9.5 6l6 6-6 6",
  locate: "M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm0-17v3m0 14v3M2 12h3m14 0h3m-10 2a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  pin: "M12 21s6.5-5.6 6.5-11a6.5 6.5 0 1 0-13 0C5.5 15.4 12 21 12 21Zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  bag: "M5.5 8h13l-1 12h-11l-1-12Zm3.5 0V6.5a3 3 0 0 1 6 0V8",
  check: "m5 12.5 4.5 4.5L19 7.5",
  code: "m8.5 8-4 4 4 4m7-8 4 4-4 4m-2-10-3 12",
  clock: "M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm0-12v4.5l3 2",
  filter: "M4 6h16M7 12h10m-7 6h4",
  monitor: "M4 5.5h16v10H4v-10Zm5 14h6m-3-4v4",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-13v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17m10-10 1.4-1.4",
  moon: "M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5Z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { readonly name: IconName; readonly size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
