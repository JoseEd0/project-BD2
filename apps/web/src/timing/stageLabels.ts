import type { StageSlice } from "./serverTiming";

/** Las etapas que mide el gestor (`src/api/tracing.py`), dichas para quien usa la app. */
const STAGE_LABELS: Readonly<Record<string, string>> = {
  parse: "Leer la consulta",
  plan: "Planificar",
  exec: "Ejecutar",
  txn: "Transacción",
  serialize: "Preparar la respuesta",
};

export const UNATTRIBUTED_LABEL = "Fuera de las etapas";

export function stageLabel(stage: Pick<StageSlice, "name" | "description">): string {
  return STAGE_LABELS[stage.name] ?? stage.description ?? stage.name;
}
