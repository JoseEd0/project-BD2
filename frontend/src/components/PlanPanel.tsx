import type { PlanInfo } from "../types";

interface PlanPanelProps {
  plan: PlanInfo | null;
}

const ACCESS_OPERATIONS = new Set([
  "SequentialScan",
  "IndexLookup",
  "IndexRange",
  "PrimaryKeyLookup",
  "PrimaryKeyRange",
]);

const EXTERNAL_OPERATIONS = new Set(["ExternalSort", "HashAggregate", "HashJoin"]);

function operationClass(operation: string): string {
  if (ACCESS_OPERATIONS.has(operation)) return "plan__op plan__op--access";
  if (EXTERNAL_OPERATIONS.has(operation)) return "plan__op plan__op--external";
  return "plan__op";
}

function PlanNode({ node, root }: { node: PlanInfo; root: boolean }) {
  return (
    <div className={root ? "plan__node plan__node--root" : "plan__node"}>
      <div className="plan__row">
        <span className={operationClass(node.operation)}>{node.operation}</span>
        {node.detail && <span className="plan__detail">{node.detail}</span>}
      </div>
      {node.children.length > 0 && (
        <div className="plan__children">
          {node.children.map((child, index) => (
            <PlanNode key={`${child.operation}-${index}`} node={child} root={false} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function PlanPanel({ plan }: PlanPanelProps) {
  return (
    <section className="panel panel--plan">
      <header className="panel__header">
        <h2 className="panel__title">Plan de ejecución</h2>
      </header>
      <div className="panel__body">
        {plan === null ? (
          <p className="panel__empty">
            El plan aparece al ejecutar un <code>SELECT</code>.
          </p>
        ) : (
          <>
            <div className="plan">
              <PlanNode node={plan} root />
            </div>
            <div className="plan__legend">
              <p>
                Se lee <strong>de abajo hacia arriba</strong>: cada operador consume las filas
                del que tiene debajo.
              </p>
              <p>
                <span className="legend-dot legend-dot--access" /> camino de acceso —
                recorrido completo, índice hash, índice B+ o el orden propio de la tabla.
              </p>
              <p>
                <span className="legend-dot legend-dot--external" /> algoritmo externo —
                se apoya en disco para no cargarlo todo en memoria.
              </p>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
