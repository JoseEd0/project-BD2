import { useEffect, useState, type RefObject } from "react";

/** El ancho se redondea a este paso: redibujar una página por cada píxel de un arrastre no sirve. */
const WIDTH_STEP_PX = 8;

/** El ancho del contenido de `element`, y sus cambios. */
export function useElementWidth(element: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = element.current;
    if (node === null) {
      return;
    }
    const measure = (measured: number) => setWidth(Math.floor(measured / WIDTH_STEP_PX) * WIDTH_STEP_PX);
    measure(node.clientWidth);
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) {
        measure(entry.contentRect.width);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [element]);
  return width;
}
