import { useCallback, useEffect, useRef, useState } from "react";

type Axis = "x" | "y";

/** Píxeles que mueve el divisor cada pulsación de una flecha del teclado. */
const KEYBOARD_STEP = 24;
const GROW_KEYS: Record<Axis, string> = { x: "ArrowRight", y: "ArrowDown" };
const SHRINK_KEYS: Record<Axis, string> = { x: "ArrowLeft", y: "ArrowUp" };

/** Cuánto crece el panel con esa tecla: hacia la derecha o abajo, crece; al revés, encoge. */
function keyboardStep(key: string, axis: Axis): number {
  if (key === GROW_KEYS[axis]) return KEYBOARD_STEP;
  if (key === SHRINK_KEYS[axis]) return -KEYBOARD_STEP;
  return 0;
}

interface Splitter {
  size: number;
  dragging: boolean;
  /** Lo que lleva el elemento divisor: se arrastra con el ratón o se mueve con las flechas. */
  separator: React.HTMLAttributes<HTMLDivElement>;
}

/** Divisor entre dos paneles, con el tamaño acotado por `min` y `max`. */
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

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const step = keyboardStep(event.key, axis);
      if (step === 0) {
        return;
      }
      event.preventDefault();
      setSize((current) => Math.min(max, Math.max(min, current + step)));
    },
    [axis, max, min],
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

  return {
    size,
    dragging,
    separator: {
      role: "separator",
      tabIndex: 0,
      "aria-orientation": axis === "x" ? "vertical" : "horizontal",
      "aria-valuemin": min,
      "aria-valuemax": max,
      "aria-valuenow": size,
      onPointerDown,
      onKeyDown,
    },
  };
}
