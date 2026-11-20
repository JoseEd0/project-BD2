import { useCallback, useEffect, useReducer, useRef } from "react";

import type { ResearchFilters } from "../api/types";
import { unexpectedFailure } from "./failures";
import { AnswerRequestError, streamAnswer } from "./streamAnswer";
import { conversationHistory, turnsReducer, type Turn } from "./turns";

/** Los turnos que se reenvían: los mismos que usa el servicio por omisión. */
const HISTORY_TURNS = 6;

export interface Research {
  readonly turns: readonly Turn[];
  readonly busy: boolean;
  readonly ask: (question: string, filters: ResearchFilters) => void;
  readonly stop: () => void;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Las preguntas de esta pestaña y la que está en curso; una a la vez. Cada pregunta lleva
 * los turnos anteriores respondidos, para que el modelo siga la conversación. */
export function useResearch(): Research {
  const [turns, dispatch] = useReducer(turnsReducer, []);
  const running = useRef<{ id: string; controller: AbortController } | null>(null);
  const latestTurns = useRef<readonly Turn[]>(turns);
  useEffect(() => {
    latestTurns.current = turns;
  }, [turns]);

  const stop = useCallback(() => {
    const current = running.current;
    if (current === null) {
      return;
    }
    current.controller.abort();
    dispatch({ type: "stopped", id: current.id, at: performance.now() });
    running.current = null;
  }, []);

  const ask = useCallback(
    (question: string, filters: ResearchFilters) => {
      stop();
      const history = conversationHistory(latestTurns.current, HISTORY_TURNS);
      const id = crypto.randomUUID();
      const controller = new AbortController();
      running.current = { id, controller };
      dispatch({ type: "asked", id, question, filters, at: performance.now() });
      streamAnswer(
        { question, filters, history },
        {
          onOpen: (serverTiming) => dispatch({ type: "opened", id, serverTiming }),
          onEvent: (event) => dispatch({ type: "event", id, event, at: performance.now() }),
        },
        controller.signal,
      )
        .then(() => dispatch({ type: "ended", id, at: performance.now() }))
        .catch((error: unknown) => {
          if (isAbort(error)) {
            return;
          }
          const failure = error instanceof AnswerRequestError ? error.failure : unexpectedFailure();
          dispatch({ type: "failed", id, failure, at: performance.now() });
        })
        .finally(() => {
          if (running.current?.id === id) {
            running.current = null;
          }
        });
    },
    [stop],
  );

  useEffect(() => () => running.current?.controller.abort(), []);

  const busy = turns.some((turn) => turn.status === "retrieving" || turn.status === "streaming");
  return { turns, busy, ask, stop };
}
