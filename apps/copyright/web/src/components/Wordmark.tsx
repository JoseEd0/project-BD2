import { Link } from "react-router";

import { APP } from "../identity";

/** Un pentagrama de tres líneas con un trazo de resaltador: la marca de la app. */
export function Wordmark() {
  return (
    <Link className="wordmark" to="/" aria-label={`${APP.name}, ${APP.tagline}: inicio`}>
      <svg className="wordmark__mark" viewBox="0 0 32 24" aria-hidden="true" focusable="false">
        <path className="wordmark__swipe" d="M11 7h15l-3 10H8Z" />
        <path className="wordmark__staff" d="M2 6h28M2 12h28M2 18h28" />
      </svg>
      <span className="wordmark__name">{APP.name}</span>
      <span className="wordmark__tagline">{APP.tagline}</span>
    </Link>
  );
}
