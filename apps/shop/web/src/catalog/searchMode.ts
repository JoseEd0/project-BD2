/** Cómo se busca: por las palabras de la ficha (BM25) o por cómo se ve la foto (CLIP). */
export type TextSearchMode = "palabras" | "aspecto";

export const TEXT_SEARCH_MODES: readonly TextSearchMode[] = ["palabras", "aspecto"];
export const DEFAULT_MODE: TextSearchMode = "palabras";

export function toSearchMode(value: string | null): TextSearchMode {
  return TEXT_SEARCH_MODES.find((mode) => mode === value) ?? DEFAULT_MODE;
}

/** Ideas para empezar: la palabra en español y la búsqueda en inglés, como está el catálogo. */
export const SUGGESTIONS: readonly { readonly label: string; readonly query: string }[] = [
  { label: "Cuerdas de guitarra", query: "guitar strings" },
  { label: "Pedal de distorsión", query: "distortion pedal" },
  { label: "Baquetas", query: "drum sticks" },
  { label: "Micrófono de condensador", query: "condenser microphone" },
  { label: "Ukelele", query: "ukulele" },
  { label: "Cable de instrumento", query: "instrument cable" },
  { label: "Teclado MIDI", query: "midi keyboard" },
  { label: "Cajón", query: "cajon" },
];
