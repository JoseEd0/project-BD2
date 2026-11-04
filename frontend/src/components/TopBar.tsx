import type { ReactNode } from "react";

import type { Theme } from "../hooks/useTheme";
import { PAGES } from "../routes";
import type { PageId } from "../routes";

interface TopBarProps {
  page: PageId;
  inTransaction: boolean;
  theme: Theme;
  onToggleTheme: () => void;
  onRefresh: () => void;
  /** Controles propios de la página, entre la navegación y el estado de la sesión. */
  children?: ReactNode;
}

/** La cabecera común: la navegación entre páginas, el estado de la transacción y el tema. */
export default function TopBar({
  page,
  inTransaction,
  theme,
  onToggleTheme,
  onRefresh,
  children,
}: TopBarProps) {
  const themeLabel = theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
  return (
    <header className="topbar">
      <div className="topbar__brand">Gestor de Base de Datos</div>
      <nav aria-label="Páginas" className="pagenav">
        {PAGES.map((item) => (
          <a
            aria-current={item.id === page ? "page" : undefined}
            className={item.id === page ? "pagenav__link pagenav__link--active" : "pagenav__link"}
            href={item.hash}
            key={item.id}
          >
            {item.label}
          </a>
        ))}
      </nav>
      {children}
      <span className="topbar__spacer" />
      <span className={inTransaction ? "badge badge--active" : "badge"}>
        <span className="badge__dot" />
        {inTransaction ? "Transacción abierta" : "Autocommit"}
      </span>
      <button className="action" onClick={onRefresh} type="button">
        Actualizar
      </button>
      <button
        aria-label={themeLabel}
        aria-pressed={theme === "dark"}
        className="action action--icon"
        onClick={onToggleTheme}
        title={themeLabel}
        type="button"
      >
        {theme === "dark" ? "☀" : "☾"}
      </button>
    </header>
  );
}
