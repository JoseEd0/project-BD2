import { useEffect, useState } from "react";

/** Cuánto se ve «Copiado» tras copiar. */
const COPIED_MS = 1500;

/** Copia un texto (el SQL de una consulta) al portapapeles y lo confirma un momento. */
export default function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) {
      return;
    }
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <button
      className="action action--quiet"
      onClick={() => void navigator.clipboard.writeText(text).then(() => setCopied(true))}
      type="button"
    >
      {copied ? "Copiado" : "Copiar"}
    </button>
  );
}
