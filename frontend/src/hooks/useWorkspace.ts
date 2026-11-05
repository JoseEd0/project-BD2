import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, ConnectionError, closeSession, fetchTables } from "../api";
import { pageHash } from "../routes";
import type { QueryFailure, QueryResponse, TableInfo } from "../types";
import { useTheme } from "./useTheme";
import type { Theme } from "./useTheme";

/** Una consulta que otra página manda al editor de la consola. `version` cambia con cada
 * envío, para que mandar dos veces la misma también la vuelva a poner. */
export interface ConsoleDraft {
  sql: string;
  version: number;
}

/**
 * Lo que comparten todas las páginas: la sesión de la pestaña —y con ella la transacción
 * abierta—, el catálogo de tablas, el estado de la conexión, el tema y la regla de una
 * petición a la vez. Una búsqueda de la página «Texto» corre dentro del `BEGIN` que se abrió
 * en la consola, igual que si se escribiera ahí.
 */
export interface Workspace {
  sessionId: string;
  tables: TableInfo[];
  /** Filas almacenadas entre todas las tablas. */
  totalRows: number;
  connected: boolean;
  inTransaction: boolean;
  running: boolean;
  theme: Theme;
  toggleTheme: () => void;
  refreshTables: () => Promise<void>;
  /** Corre la tarea si no hay otra petición de la sesión en curso; si la hay, no hace nada.
   * Dice si la corrió. */
  exclusively: (task: () => Promise<void>) => Promise<boolean>;
  /** Lo que sigue a cada respuesta: el estado de la transacción viene de ella. */
  acknowledge: (response: QueryResponse) => Promise<void>;
  /** El error para mostrar, del motor o de la conexión, ya reflejado en la transacción y la
   * conexión. Cualquier otro error se relanza. */
  acknowledgeFailure: (error: unknown) => Promise<QueryFailure>;
  /** Un error de una lectura de fondo (estructura, puntos del mapa): solo puede cambiar el
   * estado de la transacción. */
  noteBackgroundFailure: (error: unknown) => void;
  /** La última consulta que se mandó a la consola desde otra página. */
  consoleDraft: ConsoleDraft | null;
  /** Pone la consulta en el editor de la consola y la abre, sin ejecutarla. */
  openInConsole: (sql: string) => void;
}

/** Identificador de la sesión de esta pestaña. Es aleatorio de verdad: quien adivinara el de
 * otra pestaña podría confirmar o abortar su transacción. */
function newSessionId(): string {
  return `ui-${crypto.randomUUID()}`;
}

function connectionFailure(error: ConnectionError): QueryFailure {
  return {
    error: error.message,
    kind: "ConnectionError",
    line: null,
    column: null,
    in_transaction: null,
  };
}

export function useWorkspace(): Workspace {
  const [sessionId] = useState(newSessionId);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [connected, setConnected] = useState(false);
  const [inTransaction, setInTransaction] = useState(false);
  const [running, setRunning] = useState(false);
  const [consoleDraft, setConsoleDraft] = useState<ConsoleDraft | null>(null);
  const inFlight = useRef(false);
  const { theme, toggleTheme } = useTheme();

  const totalRows = useMemo(
    () => tables.reduce((sum, table) => sum + table.row_count, 0),
    [tables],
  );

  const refreshTables = useCallback(async () => {
    try {
      setTables(await fetchTables());
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    void refreshTables();
  }, [refreshTables]);

  useEffect(() => {
    const release = () => closeSession(sessionId);
    window.addEventListener("pagehide", release);
    return () => window.removeEventListener("pagehide", release);
  }, [sessionId]);

  const acknowledge = useCallback(
    async (response: QueryResponse) => {
      setInTransaction(response.in_transaction);
      setConnected(true);
      await refreshTables();
    },
    [refreshTables],
  );

  // Un error del motor dice cómo quedó la transacción: un interbloqueo o una espera
  // agotada la abortan, y la interfaz no puede seguir mostrándola abierta.
  const acknowledgeFailure = useCallback(
    async (error: unknown): Promise<QueryFailure> => {
      if (error instanceof ApiError) {
        if (error.failure.in_transaction !== null) {
          setInTransaction(error.failure.in_transaction);
        }
        await refreshTables();
        return error.failure;
      }
      if (error instanceof ConnectionError) {
        setConnected(false);
        return connectionFailure(error);
      }
      throw error;
    },
    [refreshTables],
  );

  const noteBackgroundFailure = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.failure.in_transaction !== null) {
      setInTransaction(error.failure.in_transaction);
    }
  }, []);

  // Es una ref y no estado porque dos pulsaciones seguidas de ⌘↵ llegan antes de que React
  // vuelva a pintar: una sentencia que espera un bloqueo se mandaría dos veces.
  const exclusively = useCallback(async (task: () => Promise<void>) => {
    if (inFlight.current) {
      return false;
    }
    inFlight.current = true;
    setRunning(true);
    try {
      await task();
      return true;
    } finally {
      inFlight.current = false;
      setRunning(false);
    }
  }, []);

  const openInConsole = useCallback((sql: string) => {
    setConsoleDraft((previous) => ({ sql, version: (previous?.version ?? 0) + 1 }));
    window.location.hash = pageHash("consola");
  }, []);

  return {
    sessionId,
    tables,
    totalRows,
    connected,
    inTransaction,
    running,
    theme,
    toggleTheme,
    refreshTables,
    exclusively,
    acknowledge,
    acknowledgeFailure,
    noteBackgroundFailure,
    consoleDraft,
    openInConsole,
  };
}
