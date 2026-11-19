import { APP } from "../identity";

/** El cajón visto de frente, con su boca, y el nombre en condensada. */
export function Wordmark() {
  return (
    <span className="wordmark">
      <svg className="wordmark-mark" width="30" height="30" viewBox="0 0 30 30" aria-hidden="true" focusable="false">
        <rect x="3" y="2" width="24" height="26" rx="2" fill="var(--tag)" />
        <rect x="3" y="2" width="24" height="26" rx="2" fill="none" stroke="var(--tag-ink)" strokeWidth="1.5" />
        <circle cx="15" cy="11" r="4.25" fill="var(--tolex)" />
        <path d="M7 23h16" stroke="var(--tag-ink)" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <span className="wordmark-text">
        <span className="wordmark-name">{APP.name}</span>
        <span className="wordmark-tagline">{APP.tagline}</span>
      </span>
    </span>
  );
}
