import type { Problem } from "../analysis/problem";
import { Icon } from "../components/Icon";

interface ProblemPanelProps {
  readonly problem: Problem;
  readonly onRetry: () => void;
  readonly onChooseAnother: () => void;
}

/** Por qué no hay resultado y la salida que corresponde: otro archivo o reintentar. */
export function ProblemPanel({ problem, onRetry, onChooseAnother }: ProblemPanelProps) {
  const rejected = problem.kind === "rejected";
  return (
    <section className="problem" data-kind={problem.kind} role="alert" aria-labelledby="problem-title">
      <Icon name="alert" size={24} />
      <div className="problem__body">
        <h2 id="problem-title" className="problem__title">
          {problem.title}
        </h2>
        <p className="problem__detail">{problem.detail}</p>
        {problem.status !== null && <p className="problem__status measure">HTTP {problem.status}</p>}
        <div className="problem__actions">
          {rejected ? (
            <button type="button" className="button button--primary" onClick={onChooseAnother}>
              <Icon name="upload" />
              Elegir otro audio
            </button>
          ) : (
            <button type="button" className="button button--primary" onClick={onRetry}>
              <Icon name="retry" />
              Reintentar
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
