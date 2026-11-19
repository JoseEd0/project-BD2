import { useId, useRef, type ReactNode } from "react";

import { Icon } from "./Icon";

interface RailProps {
  readonly title: ReactNode;
  readonly meta?: ReactNode;
  readonly aside?: ReactNode;
  readonly emptyText: string;
  readonly count: number;
  readonly children: ReactNode;
  readonly variant?: "fused" | "signal";
}

/** Una fila de productos con su encabezado: se desplaza de lado, con botones para el ratón. */
export function Rail({ title, meta, aside, emptyText, count, children, variant = "signal" }: RailProps) {
  const headingId = useId();
  const list = useRef<HTMLUListElement>(null);
  const scroll = (direction: 1 | -1) => {
    const element = list.current;
    element?.scrollBy({ left: direction * element.clientWidth * 0.85, behavior: "smooth" });
  };
  return (
    <section className={`rail rail-${variant}`} aria-labelledby={headingId}>
      <header className="rail-head">
        <h2 id={headingId} className="rail-title">
          {title}
        </h2>
        {meta !== undefined && <p className="rail-meta">{meta}</p>}
        {aside}
      </header>
      {count === 0 ? (
        <p className="rail-empty">{emptyText}</p>
      ) : (
        <div className="rail-body">
          <button type="button" className="rail-step rail-step-back" tabIndex={-1} aria-hidden="true" onClick={() => scroll(-1)}>
            <Icon name="left" />
          </button>
          <ul className="rail-list" ref={list}>
            {children}
          </ul>
          <button type="button" className="rail-step rail-step-next" tabIndex={-1} aria-hidden="true" onClick={() => scroll(1)}>
            <Icon name="right" />
          </button>
        </div>
      )}
    </section>
  );
}
