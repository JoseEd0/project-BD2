/** Íconos propios: trazo de 2 px con puntas redondeadas, en una cuadrícula de 24. */
const PATHS = {
  back: "M15 18l-6-6 6-6",
  close: "M6 6l12 12M18 6L6 18",
  flip: "M4 8h3l2-3h6l2 3h3v11H4zM9 13.5a3 3 0 0 0 5.4 1.8M15 12.5a3 3 0 0 0-5.4-1.8M14.5 15.5v-1.8h1.8M9.5 10.5v1.8H7.7",
  pause: "M9 6v12M15 6v12",
  play: "M8 5.5v13l10-6.5z",
  copy: "M9 9h10v10H9zM5 15V5h10",
  check: "M5 12.5l4.5 4.5L19 7.5",
  trash: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10.5 11v5M13.5 11v5",
  alert: "M12 4l9 16H3zM12 10v4M12 17.2v.3",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  cameraOff: "M4 4l16 16M9.5 5H15l2 3h3v10M4 8v11h13M10 10.5a3.5 3.5 0 0 0 4.6 4.8",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  scan: "M4 9V5h4M16 5h4v4M20 15v4h-4M8 19H4v-4M9 10h.01M15 10h.01M9.5 15c1.5 1 3.5 1 5 0",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0",
  shield: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6zM9 12l2 2 4-4",
  wifiOff: "M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 4.2-2.4M19 13a10 10 0 0 0-3.1-2M2 9.5a15 15 0 0 1 4.5-2.8M22 9.5A15 15 0 0 0 11 5.6M12 20h.01",
  arrowLeft: "M19 12H5M11 6l-6 6 6 6",
  arrowRight: "M5 12h14M13 6l6 6-6 6",
  chevron: "M9 6l6 6-6 6",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2.5v2M12 19.5v2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M2.5 12h2M19.5 12h2M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
  moon: "M19.5 14.5A8 8 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z",
  themeAuto: "M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 4v16M12 8h3.5M12 12h4.5M12 16h3.5",
  faceFront: "M12 4c-3.6 0-6 2.9-6 7 0 4.4 2.7 8 6 8s6-3.6 6-8c0-4.1-2.4-7-6-7zM9.5 10.5h.01M14.5 10.5h.01M12 11v3M10 16h4",
  faceLeft: "M14 4c-3.6 0-7 2.9-7 7 0 4.4 2.7 8 6 8 2.6 0 4.6-2.2 5.4-5.2L20 12l-1.6-1C18.2 6.9 16.6 4 14 4zM10 10.5h.01M13.5 10.5h.01M16 11.5l1.3 2.3H16M12 16h3",
  faceRight: "M10 4c3.6 0 7 2.9 7 7 0 4.4-2.7 8-6 8-2.6 0-4.6-2.2-5.4-5.2L4 12l1.6-1C5.8 6.9 7.4 4 10 4zM14 10.5h.01M10.5 10.5h.01M8 11.5l-1.3 2.3H8M9 16h3",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 24, label }: { readonly name: IconName; readonly size?: number; readonly label?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label === undefined ? undefined : "img"}
      aria-hidden={label === undefined ? true : undefined}
      aria-label={label}
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
