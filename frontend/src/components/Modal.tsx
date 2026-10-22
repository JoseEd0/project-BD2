import { useEffect, useId, useRef } from "react";
import type { MouseEvent, ReactNode } from "react";

type Width = "narrow" | "regular" | "wide";

interface ModalProps {
  title: string;
  width?: Width;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

const PANEL_CLASS: Record<Width, string> = {
  narrow: "modal__panel modal__panel--narrow",
  regular: "modal__panel",
  wide: "modal__panel modal__panel--wide",
};
const CLOSE_KEY = "Escape";
const FOCUS_KEY = "Tab";
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** El siguiente elemento enfocable dentro del panel, dando la vuelta en los extremos; `null`
 * si Tab puede seguir su curso normal. */
function wrappedFocusTarget(panel: HTMLElement, backwards: boolean): HTMLElement | null {
  const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (focusable.length === 0) {
    return panel;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  const outside = !(active instanceof Node) || !panel.contains(active) || active === panel;
  if (backwards) {
    return outside || active === first ? last : null;
  }
  return outside || active === last ? first : null;
}

/**
 * Marco común de los diálogos. Es un diálogo modal de verdad para un lector de pantalla
 * (`aria-modal`, con el título como nombre) y para el teclado: Tab da la vuelta dentro del
 * panel sin salir a la página de detrás. Se cierra con Escape o con un clic que empieza y
 * termina fuera del panel; al abrirse lleva el foco al panel y al cerrarse lo devuelve a
 * donde estaba.
 */
export default function Modal({ title, width = "regular", onClose, children, footer }: ModalProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  const pressedOnBackdrop = useRef(false);

  useEffect(() => {
    close.current = onClose;
  });

  // Una sola vez al abrir: quien lo usa pasa un `onClose` nuevo en cada render, y repetir
  // esto en cada uno le robaría el foco a lo que se esté escribiendo dentro del diálogo.
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === CLOSE_KEY) {
        close.current();
        return;
      }
      if (event.key !== FOCUS_KEY || panel.current === null) {
        return;
      }
      const target = wrappedFocusTarget(panel.current, event.shiftKey);
      if (target !== null) {
        event.preventDefault();
        target.focus();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previous?.focus();
    };
  }, []);

  // Solo cierra un clic que empezó en el fondo: seleccionar el texto de un campo y soltar
  // el ratón fuera del panel también termina en un `click` sobre el fondo.
  function rememberPress(event: MouseEvent<HTMLDivElement>) {
    pressedOnBackdrop.current = event.target === event.currentTarget;
  }

  function closeOnBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (pressedOnBackdrop.current && event.target === event.currentTarget) {
      onClose();
    }
    pressedOnBackdrop.current = false;
  }

  return (
    <div
      className="modal"
      onClick={closeOnBackdropClick}
      onMouseDown={rememberPress}
      role="presentation"
    >
      <div
        aria-labelledby={titleId}
        aria-modal="true"
        className={PANEL_CLASS[width]}
        ref={panel}
        role="dialog"
        tabIndex={-1}
      >
        <header className="modal__header">
          <h2 id={titleId}>{title}</h2>
          <button aria-label="Cerrar" className="icon-button" onClick={onClose} type="button">
            ✕
          </button>
        </header>
        {children}
        {footer && <footer className="modal__footer">{footer}</footer>}
      </div>
    </div>
  );
}
