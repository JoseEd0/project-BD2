import { Icon } from "./Icon";

interface ProblemNoticeProps {
  readonly title: string;
  readonly detail: string;
  readonly onRetry?: (() => void) | undefined;
  readonly retrying?: boolean;
}

/** Un fallo con su motivo y, si sirve de algo, la salida. */
export function ProblemNotice({ title, detail, onRetry, retrying = false }: ProblemNoticeProps) {
  return (
    <div className="problem" role="alert">
      <Icon name="alert" />
      <div className="problem-text">
        <p className="problem-title">{title}</p>
        <p>{detail}</p>
      </div>
      {onRetry !== undefined && (
        <button type="button" className="button button-quiet" onClick={onRetry} disabled={retrying}>
          <Icon name="retry" size={18} />
          {retrying ? "Reintentando…" : "Reintentar"}
        </button>
      )}
    </div>
  );
}
