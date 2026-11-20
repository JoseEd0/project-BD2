import { formatCount } from "../format";

const STAR_COUNT = 5;
const FULL_PERCENT = 100;

/** La valoración como cinco estrellas rellenas hasta su fracción, con el número de opiniones. */
export function Stars({ rating, count }: { readonly rating: number | null; readonly count: number | null }) {
  if (rating === null) {
    return <span className="stars-none">Sin opiniones</span>;
  }
  const filled = `${(Math.min(rating, STAR_COUNT) / STAR_COUNT) * FULL_PERCENT}%`;
  const label = `${rating.toLocaleString("es-PE", { maximumFractionDigits: 1 })} de 5 estrellas`;
  return (
    <span className="stars">
      <span className="stars-track" role="img" aria-label={label}>
        <span className="stars-fill" style={{ width: filled }} />
      </span>
      <span className="stars-value">{rating.toLocaleString("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
      {count !== null && <span className="stars-count">({formatCount(count)})</span>}
    </span>
  );
}
