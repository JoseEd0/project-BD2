import { useCallback, useEffect, useRef, useState } from "react";

import { runQuery } from "../api";
import {
  RANKING_METHODS,
  answeredOutcome,
  failedOutcome,
  searchSql,
} from "../lib/textSearch";
import type { RankingOutcome, ResultFields, SearchRequest } from "../lib/textSearch";
import type { Workspace } from "./useWorkspace";

/** Cuánto se espera tras la última tecla antes de consultar: lo bastante para no mandar una
 * consulta por letra, y poco para que la respuesta se sienta inmediata. */
const TYPING_PAUSE_MS = 200;
/** Cuánto se espera para reintentar si otra petición de la sesión (la consola) estaba en
 * curso. */
const BUSY_RETRY_MS = 150;

/** Una búsqueda lista para consultar: la petición sin el ranking y cómo leer sus filas. */
export interface ReadySearch {
  request: Omit<SearchRequest, "method">;
  fields: ResultFields;
}

/** Los `k1` y `b` con que el motor puntuó la última búsqueda con BM25. */
export interface Bm25Parameters {
  k1: number | null;
  b: number | null;
}

export interface LiveSearch {
  outcomes: RankingOutcome[];
  /** Hay una búsqueda pendiente o en curso cuyo resultado todavía no se ve. */
  searching: boolean;
  bm25: Bm25Parameters;
  /** Consulta ya, sin esperar la pausa al escribir. */
  searchNow: () => void;
}

function searchKey(search: ReadySearch | null): string | null {
  return search === null ? null : JSON.stringify(search.request);
}

/**
 * Busca mientras se escribe, con los dos rankings, por `POST /query` y dentro de la sesión.
 * Las consultas van de una en una: si lo escrito cambia mientras una corre, al terminar se
 * consulta lo último, y solo se muestra el resultado de lo que se pidió por última vez.
 */
export function useLiveSearch(search: ReadySearch | null, workspace: Workspace): LiveSearch {
  const { exclusively, sessionId, acknowledge, acknowledgeFailure } = workspace;
  const [outcomes, setOutcomes] = useState<RankingOutcome[]>([]);
  const [shownKey, setShownKey] = useState<string | null>(null);
  const [bm25, setBm25] = useState<Bm25Parameters>({ k1: null, b: null });
  const wanted = useRef<ReadySearch | null>(search);
  const answeredKey = useRef<string | null>(null);
  const draining = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const key = searchKey(search);
  wanted.current = search;

  const answer = useCallback(
    async (ready: ReadySearch): Promise<RankingOutcome[]> => {
      const answered: RankingOutcome[] = [];
      for (const method of RANKING_METHODS) {
        const sql = searchSql({ ...ready.request, method });
        try {
          const response = await runQuery(sql, sessionId);
          await acknowledge(response);
          answered.push(answeredOutcome(method, sql, response, ready.fields));
          if (method === "BM25" && response.text !== null) {
            setBm25({ k1: response.text.k1, b: response.text.b });
          }
        } catch (error) {
          answered.push(failedOutcome(method, sql, await acknowledgeFailure(error)));
        }
      }
      return answered;
    },
    [acknowledge, acknowledgeFailure, sessionId],
  );

  const drain = useCallback(async () => {
    if (draining.current) {
      return;
    }
    draining.current = true;
    try {
      for (;;) {
        const ready = wanted.current;
        const readyKey = searchKey(ready);
        if (ready === null || readyKey === answeredKey.current) {
          return;
        }
        let answered: RankingOutcome[] = [];
        const ran = await exclusively(async () => {
          answered = await answer(ready);
        });
        if (!ran) {
          window.setTimeout(() => void drain(), BUSY_RETRY_MS);
          return;
        }
        answeredKey.current = readyKey;
        // Mientras se borraba la consulta no hay nada que mostrar; si solo cambió, el
        // resultado anterior queda a la vista hasta que llegue el nuevo.
        if (wanted.current !== null) {
          setOutcomes(answered);
          setShownKey(readyKey);
        }
      }
    } finally {
      draining.current = false;
    }
  }, [answer, exclusively]);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (key === null) {
      answeredKey.current = null;
      setOutcomes([]);
      setShownKey(null);
      return;
    }
    timer.current = window.setTimeout(() => void drain(), TYPING_PAUSE_MS);
    return () => window.clearTimeout(timer.current);
  }, [key, drain]);

  const searchNow = useCallback(() => {
    window.clearTimeout(timer.current);
    void drain();
  }, [drain]);

  return { outcomes, searching: key !== null && key !== shownKey, bm25, searchNow };
}
