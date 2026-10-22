import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

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

/**
 * Marco común de los diálogos. Es un diálogo modal de verdad para un lector de pantalla
 * (`aria-modal`, con el título como nombre), se cierra con Escape o pulsando fuera, y al
 * abrirse lleva el foco al panel; al cerrarse, lo devuelve a donde estaba.
 */
export default function Modal({ title, width = "regular", onClose, children, footer }: ModalProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);

  useEffect(() => {
    close.current = onClose;
  });

  // Una sola vez al abrir: quien lo usa pasa un `onClose` nuevo en cada render, y repetir
  // esto en cada uno le robaría el foco a lo que se esté escribiendo dentro del diálogo.
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === CLOSE_KEY) {
        close.current();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  }, []);

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div
        aria-labelledby={titleId}
        aria-modal="true"
        className={PANEL_CLASS[width]}
        onClick={(event) => event.stopPropagation()}
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
