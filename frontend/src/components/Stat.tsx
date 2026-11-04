/** Una cifra con su etiqueta, en las fichas de estructura física. */
export default function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span className="stat__value">
        {typeof value === "number" ? value.toLocaleString("es") : value}
      </span>
      <span className="stat__label">{label}</span>
    </div>
  );
}
