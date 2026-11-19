/** Preguntas reales sobre el corpus: muestran qué se puede preguntar y en qué idiomas. */
const EXAMPLES: readonly string[] = [
  "¿Cómo reemplazan los índices aprendidos a los árboles B?",
  "approximate nearest neighbor search with HNSW graphs in vector databases",
  "¿Cómo se evalúan los sistemas de recomendación secuencial?",
];

export function EmptyConversation({ onAsk, disabled }: { readonly onAsk: (question: string) => void; readonly disabled: boolean }) {
  return (
    <section className="empty" aria-labelledby="empty-title">
      <h1 id="empty-title">Pregunta a los papers y comprueba cada cita en su página.</h1>
      <p>
        Las respuestas salen de papers de arXiv sobre bases de datos y recuperación de información y del material del curso. Cada número entre
        corchetes abre el PDF en la página de donde sale la afirmación, con el pasaje marcado.
      </p>
      <ul className="empty__examples" aria-label="Preguntas de ejemplo">
        {EXAMPLES.map((example) => (
          <li key={example}>
            <button type="button" className="example" disabled={disabled} onClick={() => onAsk(example)}>
              {example}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
