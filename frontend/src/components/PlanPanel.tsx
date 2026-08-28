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

function PlanNode({ node, root }: { node: PlanInfo; root: boolean }) {
  const accessPath = ACCESS_OPERATIONS.has(node.operation);
  return (
    <div className={root ? "plan__node plan__node--root" : "plan__node"}>
      <div className="plan__row">
        <span className={accessPath ? "plan__op plan__op--access" : "plan__op"}>
          {node.operation}
        </span>
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
            <p className="plan__legend">
              En verde, el <strong>camino de acceso</strong> elegido: recorrido completo,
              índice hash, índice B+ o el orden propio de la tabla. Encima van los operadores
              que consumen esas filas, de abajo hacia arriba.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
