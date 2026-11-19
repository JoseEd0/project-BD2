import type { Detection } from "../api/types";
import { formatClock } from "../analysis/format";
import { verdictOf } from "../analysis/verdict";
import { Icon, type IconName } from "../components/Icon";

const TONE_ICONS: Record<ReturnType<typeof verdictOf>["tone"], IconName> = {
  copy: "marker",
  weak: "marker",
  clear: "check",
  silent: "wave",
};

/** La conclusión en una frase, y debajo lo que se analizó. */
export function VerdictLine({ detection }: { readonly detection: Detection }) {
  const verdict = verdictOf(detection);
  return (
    <section className="verdict" data-tone={verdict.tone} aria-labelledby="verdict-title">
      <Icon name={TONE_ICONS[verdict.tone]} size={28} />
      <div>
        <h2 id="verdict-title" className="verdict__headline" tabIndex={-1}>
          {verdict.headline}
        </h2>
        <p className="verdict__detail">{verdict.detail}</p>
        <p className="verdict__facts measure">
          {detection.format} · {formatClock(detection.duration_seconds)} · {detection.windows} ventanas de {detection.window_seconds} s
          {detection.silent_windows > 0 && ` · ${detection.silent_windows} en silencio`}
        </p>
      </div>
    </section>
  );
}
