/** Iconos propios, un solo trazo de 1,75 px sobre una rejilla de 24, del color del texto. */
const PATHS = {
  upload: "M12 15V4m0 0-4.5 4.5M12 4l4.5 4.5M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15",
  play: "M8 5.5v13a.6.6 0 0 0 .9.5l10.2-6.5a.6.6 0 0 0 0-1L8.9 5a.6.6 0 0 0-.9.5Z",
  pause: "M8.5 5v14M15.5 5v14",
  swap: "M7 7h11m0 0-3.5-3.5M18 7l-3.5 3.5M17 17H6m0 0 3.5 3.5M6 17l3.5-3.5",
  close: "M6 6l12 12M18 6 6 18",
  alert: "M12 8.5v4.5m0 3.2v.1M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  check: "m5 12.5 4.5 4.5L19 7.5",
  retry: "M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4v4.5H9",
  marker: "M4 20h6M14.5 4.5l5 5L10 19H5v-5l9.5-9.5Z",
  back: "M11 7 6 12l5 5M18 7l-5 5 5 5",
  forward: "m13 7 5 5-5 5M6 7l5 5-5 5",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm4.6-1.9L20 20",
  similar: "M4 14c2-4 4-4 6 0s4 4 6 0 3-3 4-2M4 9c2-4 4-4 6 0s4 4 6 0 3-3 4-2",
  wave: "M3 12h2m2-4v8m3-11v14m3-10v6m3-9v12m3-8v4m2-2h1",
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
