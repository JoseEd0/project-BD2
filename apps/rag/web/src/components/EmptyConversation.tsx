/** Preguntas reales sobre el corpus: muestran qué se puede preguntar y en qué idiomas. */
const EXAMPLES: readonly string[] = [
  "¿Cómo reemplazan los índices aprendidos a los árboles B?",
  "¿Qué ventajas tiene HNSW frente a IVF en búsqueda vectorial?",
  "¿Cómo combina BM25 la frecuencia del término con el largo del documento?",
  "How are sequential recommender systems evaluated?",
];

export function EmptyConversation({ onAsk, disabled }: { readonly onAsk: (question: string) => void; readonly disabled: boolean }) {
  return (
    <section className="empty" aria-labelledby="empty-title">
      <h1 id="empty-title">¿Qué quieres saber de bases de datos o recuperación de información?</h1>
      <p>
        Respondo con papers de arXiv sobre el área y con el material del curso. Cada número entre corchetes abre el PDF en la página de donde sale la
        afirmación. Puedes repreguntar: recuerdo lo que hablamos.
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
