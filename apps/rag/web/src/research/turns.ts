import type { ResearchFilters } from "../api/types";
import type { AnswerEvent, AnswerSource, DoneData, FallbackData } from "./answerEvents";
import { failureOfEvent, interruptedFailure, type Failure } from "./failures";

/**
 * Una pregunta y lo que pasó con ella. Los estados, en orden:
 *
 *     retrieving ─sources─► streaming ─done─► answered
 *          │                    │
 *          └────── error ───────┴──────────► failed     (y `stopped` si quien pregunta corta)
 */
export type TurnStatus = "retrieving" | "streaming" | "answered" | "failed" | "stopped";

export interface Turn {
  readonly id: string;
  readonly question: string;
  readonly filters: ResearchFilters;
  readonly status: TurnStatus;
  readonly model: string | null;
  readonly sources: readonly AnswerSource[];
  /** La consulta con que el servicio buscó los fragmentos, cuando llega con `sources`. */
  readonly searchQuery: string | null;
  readonly text: string;
  readonly done: DoneData | null;
  readonly fallback: FallbackData | null;
  readonly failure: Failure | null;
  readonly serverTiming: string | null;
  readonly startedAt: number;
  /** Desde que se pidió hasta el primer texto y hasta el cierre, medidos en el navegador. */
  readonly firstTextMs: number | null;
  readonly totalMs: number | null;
}

export type TurnAction =
  | { readonly type: "asked"; readonly id: string; readonly question: string; readonly filters: ResearchFilters; readonly at: number }
  | { readonly type: "opened"; readonly id: string; readonly serverTiming: string | null }
  | { readonly type: "event"; readonly id: string; readonly event: AnswerEvent; readonly at: number }
  | { readonly type: "ended"; readonly id: string; readonly at: number }
  | { readonly type: "failed"; readonly id: string; readonly failure: Failure; readonly at: number }
  | { readonly type: "stopped"; readonly id: string; readonly at: number };

const SETTLED: ReadonlySet<TurnStatus> = new Set<TurnStatus>(["answered", "failed", "stopped"]);

export function isSettled(turn: Turn): boolean {
  return SETTLED.has(turn.status);
}

function newTurn(action: Extract<TurnAction, { type: "asked" }>): Turn {
  return {
    id: action.id,
    question: action.question,
    filters: action.filters,
    status: "retrieving",
    model: null,
    sources: [],
    searchQuery: null,
    text: "",
    done: null,
    fallback: null,
    failure: null,
    serverTiming: null,
    startedAt: action.at,
    firstTextMs: null,
    totalMs: null,
  };
}

function elapsed(turn: Turn, at: number): number {
  return Math.max(0, at - turn.startedAt);
}

function settle(turn: Turn, status: TurnStatus, at: number, failure: Failure | null = null): Turn {
  return { ...turn, status, failure, totalMs: elapsed(turn, at) };
}

function applyEvent(turn: Turn, event: AnswerEvent, at: number): Turn {
  switch (event.type) {
    case "sources":
      return {
        ...turn,
        status: "streaming",
        model: event.data.model,
        sources: event.data.sources,
        searchQuery: event.data.search_query ?? null,
      };
    case "delta":
      return {
        ...turn,
        status: "streaming",
        text: turn.text + event.text,
        firstTextMs: turn.firstTextMs ?? elapsed(turn, at),
      };
    case "fallback":
      return { ...turn, fallback: event.data, model: event.data.to_model };
    case "done":
      return { ...settle(turn, "answered", at), done: event.data, text: event.data.answer, model: event.data.model };
    case "error":
      return settle(turn, "failed", at, failureOfEvent(event.data.kind, event.data.error, event.data.request_id));
  }
}

function updateTurn(turn: Turn, action: Exclude<TurnAction, { type: "asked" }>): Turn {
  if (isSettled(turn)) {
    return turn;
  }
  switch (action.type) {
    case "opened":
      return { ...turn, serverTiming: action.serverTiming };
    case "event":
      return applyEvent(turn, action.event, action.at);
    case "ended":
      // Un cuerpo que se cierra sin `done` ni `error` es una conexión cortada.
      return settle(turn, "failed", action.at, interruptedFailure());
    case "failed":
      return settle(turn, "failed", action.at, action.failure);
    case "stopped":
      return settle(turn, "stopped", action.at);
  }
}

/** Un turno anterior tal como lo recibe el servicio para seguir la conversación. */
export interface Exchange {
  readonly question: string;
  readonly answer: string;
}

/**
 * Los últimos `limit` turnos respondidos, para que el modelo entienda un seguimiento. El
 * servicio vuelve a acotarlos (`RAG_ANSWER_HISTORY_TURNS`); este tope solo evita mandar una
 * conversación larga entera.
 */
export function conversationHistory(turns: readonly Turn[], limit: number): Exchange[] {
  return turns
    .filter((turn) => turn.status === "answered" && turn.done?.complete === true && turn.text !== "")
    .slice(-limit)
    .map((turn) => ({ question: turn.question, answer: turn.text }));
}

/** Las preguntas de la sesión, de la más vieja a la más nueva. */
export function turnsReducer(turns: readonly Turn[], action: TurnAction): readonly Turn[] {
  if (action.type === "asked") {
    return [...turns, newTurn(action)];
  }
  return turns.map((turn) => (turn.id === action.id ? updateTurn(turn, action) : turn));
}
