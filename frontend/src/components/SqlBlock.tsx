import { useMemo } from "react";

import { tokenize } from "../lib/highlight";

/** SQL de solo lectura con el mismo resaltado que el editor de la consola. */
export default function SqlBlock({ sql }: { sql: string }) {
  const tokens = useMemo(() => tokenize(sql), [sql]);
  return (
    <pre className="sqlblock">
      {tokens.map((token, index) => (
        <span className={`tok tok--${token.kind}`} key={index}>
          {token.text}
        </span>
      ))}
    </pre>
  );
}
