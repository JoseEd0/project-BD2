import { useCallback, useEffect, useRef, useState } from "react";

import { runQuery } from "../api";
import { mediaCards, similaritySql } from "../lib/mediaSearch";
import type { MediaRequest, PanelOutcome } from "../lib/mediaSearch";
import type { Workspace } from "./useWorkspace";

/** Pausa tras el último cambio (un parámetro que se escribe) antes de consultar. */
const CHANGE_PAUSE_MS = 250;
/** Reintento si otra petición de la sesión (la consola) estaba en curso. */
const BUSY_RETRY_MS = 150;

export interface SimilaritySearch {
  outcomes: (PanelOutcome | null)[];
  /** Hay una búsqueda pendiente o en curso cuyo resultado todavía no se ve. */
  searching: boolean;
}

function requestsKey(requests: readonly (MediaRequest | null)[]): string | null {
  return requests.every((request) => request === null) ? null : JSON.stringify(requests);
}

/**
 * Corre las dos consultas `SIMILAR_TO` de la página por `POST /query`, dentro de la sesión y
 * de una en una. Si algo cambia mientras corren, al terminar se consulta lo último, y solo
 * se muestra el resultado de lo que se pidió por última vez.
 */
export function useSimilaritySearch(
  requests: readonly (MediaRequest | null)[],
  workspace: Workspace,
): SimilaritySearch {
  const { exclusively, sessionId, acknowledge, acknowledgeFailure } = workspace;
  const [outcomes, setOutcomes] = useState<(PanelOutcome | null)[]>([]);
  const [shownKey, setShownKey] = useState<string | null>(null);
  const wanted = useRef(requests);
  const answeredKey = useRef<string | null>(null);
  const draining = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const key = requestsKey(requests);
  wanted.current = requests;

  const answer = useCallback(
    async (pending: readonly (MediaRequest | null)[]): Promise<(PanelOutcome | null)[]> => {
      const answered: (PanelOutcome | null)[] = [];
      for (const request of pending) {
        if (request === null) {
          answered.push(null);
          continue;
        }
        const sql = similaritySql(request);
        try {
          const response = await runQuery(sql, sessionId);
          await acknowledge(response);
          answered.push({
            sql,
            cards: mediaCards(response, request.target),
            plan: response.plan,
            failure: null,
            elapsedMs: response.elapsed_ms,
          });
        } catch (error) {
          const failure = await acknowledgeFailure(error);
          answered.push({ sql, cards: [], plan: null, failure, elapsedMs: null });
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
        const pending = wanted.current;
        const pendingKey = requestsKey(pending);
        if (pendingKey === null || pendingKey === answeredKey.current) {
          return;
        }
        let answered: (PanelOutcome | null)[] = [];
        const ran = await exclusively(async () => {
          answered = await answer(pending);
        });
        if (!ran) {
          window.setTimeout(() => void drain(), BUSY_RETRY_MS);
          return;
        }
        answeredKey.current = pendingKey;
        setOutcomes(answered);
        setShownKey(pendingKey);
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
    timer.current = window.setTimeout(() => void drain(), CHANGE_PAUSE_MS);
    return () => window.clearTimeout(timer.current);
  }, [key, drain]);

  return { outcomes, searching: key !== null && key !== shownKey };
}
