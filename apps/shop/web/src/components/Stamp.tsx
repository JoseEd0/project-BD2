import type { Signal } from "../recommend/signals";
import { SIGNAL_WORDING } from "../recommend/signals";

/** El sello de goma de una señal: su tinta y su nombre, nunca solo el color. */
export function Stamp({ signal }: { readonly signal: Signal }) {
  return <span className={`stamp stamp-${signal}`}>{SIGNAL_WORDING[signal].stamp}</span>;
}

/** El sello que marca lo que cambió después de una compra. */
export function NewStamp() {
  return <span className="stamp stamp-new">Nuevo</span>;
}
