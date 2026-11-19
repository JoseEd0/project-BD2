import { useState } from "react";

import { Icon } from "./Icon";

type CopyState = "idle" | "copied" | "failed";

const COPY_RESET_MS = 2_500;

/** Copia el código al portapapeles y lo dice; si el navegador no deja, pide copiarlo a mano. */
export function CopyCode({ code }: { readonly code: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setState("copied");
    } catch {
      setState("failed");
    }
    window.setTimeout(() => setState("idle"), COPY_RESET_MS);
  };
  return (
    <>
      <button type="button" className="button button-secondary" onClick={() => void copy()}>
        <Icon name={state === "copied" ? "check" : "copy"} />
        {state === "copied" ? "Copiado" : "Copiar código"}
      </button>
      <p className="visually-hidden" role="status">
        {state === "copied" ? "Código copiado" : state === "failed" ? "No se pudo copiar: selecciónalo y cópialo a mano." : ""}
      </p>
      {state === "failed" && <p className="field-error">No se pudo copiar: selecciónalo y cópialo a mano.</p>}
    </>
  );
}
