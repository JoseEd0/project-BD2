import { useEffect, useState } from "react";

import { ApiError, fetchStructure } from "../api";
import type { SearchTarget } from "../lib/textSearch";
import type { InvertedStructure } from "../types";

export interface InvertedIndexState {
  name: string | null;
  structure: InvertedStructure | null;
  error: string | null;
}

const EMPTY: InvertedIndexState = { name: null, structure: null, error: null };

/**
 * La forma del índice invertido de la columna, leída dentro de la sesión. Se vuelve a leer
 * cuando cambia `version`: la página le pasa algo que cambia cuando la tabla cambia (sus
 * filas o sus índices), para no releer el léxico entero en cada búsqueda.
 */
export function useInvertedIndex(
  target: SearchTarget | null,
  version: string,
  sessionId: string,
  onFailure: (error: unknown) => void,
): InvertedIndexState {
  const [state, setState] = useState<InvertedIndexState>(EMPTY);
  const table = target?.table ?? null;
  const column = target?.column ?? null;

  useEffect(() => {
    if (table === null) {
      setState(EMPTY);
      return;
    }
    let current = true;
    fetchStructure(table, sessionId)
      .then((structure) => {
        const index = structure.indexes.find(
          (item) => item.column === column && item.structure.kind === "inverted",
        );
        if (current) {
          setState(
            index === undefined || index.structure.kind !== "inverted"
              ? EMPTY
              : { name: index.name, structure: index.structure, error: null },
          );
        }
      })
      .catch((reason: unknown) => {
        if (current) {
          const message =
            reason instanceof ApiError ? reason.failure.error : "No se pudo leer el índice";
          setState({ name: null, structure: null, error: message });
        }
        onFailure(reason);
      });
    return () => {
      current = false;
    };
  }, [table, column, version, sessionId, onFailure]);

  return state;
}
