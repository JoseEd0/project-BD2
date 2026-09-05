import { useCallback, useEffect, useRef, useState } from "react";

type Axis = "x" | "y";

interface Splitter {
  size: number;
  dragging: boolean;
  onPointerDown: (event: React.PointerEvent) => void;
}

/** Divisor arrastrable entre dos paneles, con el tamaño acotado por `min` y `max`. */
export function useSplitter(initial: number, min: number, max: number, axis: Axis): Splitter {
  const [size, setSize] = useState(initial);
  const [dragging, setDragging] = useState(false);
  const origin = useRef({ position: 0, size: initial });

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      event.preventDefault();
      origin.current = {
        position: axis === "x" ? event.clientX : event.clientY,
        size,
      };
      setDragging(true);
    },
    [axis, size],
  );

  useEffect(() => {
    if (!dragging) {
      return;
    }
    const move = (event: PointerEvent) => {
      const current = axis === "x" ? event.clientX : event.clientY;
      const next = origin.current.size + (current - origin.current.position);
      setSize(Math.min(max, Math.max(min, next)));
    };
    const stop = () => setDragging(false);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [axis, dragging, max, min]);

  return { size, dragging, onPointerDown };
}
