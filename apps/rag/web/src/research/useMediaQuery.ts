import { useSyncExternalStore } from "react";

/** Si `query` se cumple ahora, y el cambio en vivo (rotar el celular, achicar la ventana). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
