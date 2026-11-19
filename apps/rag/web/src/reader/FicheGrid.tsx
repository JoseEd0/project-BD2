import { Icon } from "../components/Icon";

/** Cuadros por fila, como las columnas de una microficha. */
const FRAMES_PER_ROW = 10;
const ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

interface FicheGridProps {
  readonly pages: number;
  readonly current: number;
  readonly cited: ReadonlySet<number>;
  readonly passagePages: ReadonlySet<number>;
  readonly onSelect: (page: number) => void;
}

function rowLabel(row: number): string {
  return ROW_LETTERS.charAt(row % ROW_LETTERS.length).repeat(Math.floor(row / ROW_LETTERS.length) + 1);
}

/**
 * Las páginas del paper como los cuadros de una microficha: las que la respuesta cita,
 * llenas; la abierta, recuadrada; las del fragmento abierto, marcadas. Cada cuadro abre su
 * página.
 */
export function FicheGrid({ pages, current, cited, passagePages, onSelect }: FicheGridProps) {
  const rows = Math.ceil(pages / FRAMES_PER_ROW);
  return (
    <nav className="fiche" aria-label="Páginas del paper">
      <div className="fiche__nav">
        <button type="button" className="button button--icon" aria-label="Página anterior" disabled={current <= 1} onClick={() => onSelect(current - 1)}>
          <Icon name="previous" />
        </button>
        <p className="fiche__position">
          Página <strong>{current}</strong> de {pages}
        </p>
        <button type="button" className="button button--icon" aria-label="Página siguiente" disabled={current >= pages} onClick={() => onSelect(current + 1)}>
          <Icon name="next" />
        </button>
      </div>
      <ol className="fiche__frames">
        {Array.from({ length: rows }, (_, row) => (
          <li key={row} className="fiche__row">
            <span className="fiche__letter" aria-hidden="true">
              {rowLabel(row)}
            </span>
            <ol>
              {Array.from({ length: Math.min(FRAMES_PER_ROW, pages - row * FRAMES_PER_ROW) }, (_, column) => {
                const page = row * FRAMES_PER_ROW + column + 1;
                const state = [cited.has(page) ? "citada" : null, passagePages.has(page) ? "del fragmento" : null].filter(Boolean).join(", ");
                return (
                  <li key={page}>
                    <button
                      type="button"
                      className="frame"
                      data-cited={cited.has(page) || undefined}
                      data-passage={passagePages.has(page) || undefined}
                      aria-current={page === current ? "page" : undefined}
                      aria-label={`Página ${page}${state === "" ? "" : `, ${state}`}`}
                      onClick={() => onSelect(page)}
                    >
                      {page}
                    </button>
                  </li>
                );
              })}
            </ol>
          </li>
        ))}
      </ol>
    </nav>
  );
}
