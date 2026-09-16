import { useLayoutEffect, useMemo, useRef } from "react";
import type { KeyboardEvent, UIEvent } from "react";

import { tokenize } from "../lib/highlight";

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
}

const TAB = "  ";

/**
 * Editor con numeración de líneas y resaltado. La técnica: un `<pre>` coloreado debajo y
 * el `<textarea>` real encima con el texto transparente. Ambos comparten tipografía y
 * métricas, así que el cursor cae justo donde parece.
 */
export default function SqlEditor({ value, onChange, onRun }: SqlEditorProps) {
  const highlightRef = useRef<HTMLPreElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const tokens = useMemo(() => tokenize(value), [value]);
  const lineCount = useMemo(() => value.split("\n").length, [value]);

  useLayoutEffect(() => {
    syncScroll();
  }, [value]);

  function syncScroll() {
    const source = textareaRef.current;
    if (!source) return;
    if (highlightRef.current) {
      highlightRef.current.scrollTop = source.scrollTop;
      highlightRef.current.scrollLeft = source.scrollLeft;
    }
    if (gutterRef.current) {
      gutterRef.current.scrollTop = source.scrollTop;
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      onRun();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      const target = event.currentTarget;
      const { selectionStart, selectionEnd } = target;
      const next = value.slice(0, selectionStart) + TAB + value.slice(selectionEnd);
      onChange(next);
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = selectionStart + TAB.length;
      });
    }
  }

  return (
    <div className="code">
      <div className="code__gutter" ref={gutterRef}>
        {Array.from({ length: lineCount }, (_, index) => (
          <span key={index}>{index + 1}</span>
        ))}
      </div>
      <div className="code__surface">
        <pre aria-hidden="true" className="code__highlight" ref={highlightRef}>
          {tokens.map((token, index) => (
            <span className={`tok tok--${token.kind}`} key={index}>
              {token.text}
            </span>
          ))}
          {"\n"}
        </pre>
        <textarea
          className="code__input"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          onScroll={(event: UIEvent<HTMLTextAreaElement>) => {
            void event;
            syncScroll();
          }}
          placeholder="Escribe una consulta o elige un atajo de abajo. Ejemplo: SELECT * FROM clientes LIMIT 10;"
          ref={textareaRef}
          spellCheck={false}
          value={value}
        />
      </div>
    </div>
  );
}
