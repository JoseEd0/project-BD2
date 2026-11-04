import { useMemo } from "react";

import { highlightSegments } from "../../lib/textHighlight";

/** El texto con las palabras de la consulta marcadas con `<mark>`. */
export default function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  const segments = useMemo(() => highlightSegments(text, terms), [text, terms]);
  return (
    <>
      {segments.map((segment, index) =>
        segment.match ? (
          <mark className="hit" key={index}>
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}
