/**
 * Preguntas reales sobre el corpus: muestran qué se puede preguntar y en qué idiomas. Las de
 * los papers nombran sus términos en inglés («learned indexes», «B-tree»): con «índices
 * aprendidos» o «árboles B», BM25 y los embeddings solo encuentran el material del curso, el
 * único en español.
 */
const EXAMPLES: readonly string[] = [
  "¿Qué ventajas tienen los learned indexes frente a un B-tree?",
  "¿Qué ventajas tiene HNSW frente a IVF en búsqueda vectorial?",
  "¿Qué resultados obtuvo el informe al comparar el R-Tree con GiST?",
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
