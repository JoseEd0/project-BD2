import { useEffect, useState, type RefObject } from "react";

import type { Size } from "./geometry";

/** El tamaño en píxeles CSS de un elemento, al día con cada cambio (girar el celular). */
export function useViewSize(element: RefObject<HTMLElement | null>): Size | null {
  const [size, setSize] = useState<Size | null>(null);
  useEffect(() => {
    const target = element.current;
    if (target === null) {
      return;
    }
    const measure = () => setSize({ width: target.clientWidth, height: target.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(target);
    return () => observer.disconnect();
  }, [element]);
  return size;
}
