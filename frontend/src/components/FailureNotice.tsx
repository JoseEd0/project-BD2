import type { QueryFailure } from "../types";

/** Un error del motor o de la conexión: su tipo, el mensaje y, si el parser la dio, la
 * posición —o el extracto de la línea con el cursor bajo la columna—. */
export default function FailureNotice({ failure }: { failure: QueryFailure }) {
  const [headline, ...excerpt] = failure.error.split("\n");
  return (
    <div className="notice notice--error">
      <div className="notice__title">{failure.kind}</div>
      <div>{headline}</div>
      {excerpt.length > 0 && <pre className="notice__excerpt">{excerpt.join("\n")}</pre>}
      {excerpt.length === 0 && failure.line !== null && (
        <div className="notice__where">
          línea {failure.line}, columna {failure.column}
        </div>
      )}
    </div>
  );
}
