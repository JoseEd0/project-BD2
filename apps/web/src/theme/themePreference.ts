/**
 * Preferencia de tema del portal: la del sistema (por omisión), clara u oscura.
 *
 * A diferencia de la consola del gestor, que alterna entre dos temas, el portal sigue al
 * sistema mientras el usuario no elija otra cosa, y sigue sus cambios en vivo. La misma
 * función que aplica la preferencia corre antes de pintar (Vite la incrusta en `index.html`
 * con `themeBootScript`) y después en cada cambio, así que solo hay una lógica.
 */

export const THEME_PREFERENCES = ["system", "light", "dark"] as const;

export type ThemePreference = (typeof THEME_PREFERENCES)[number];

export const THEME_STORAGE_KEY = "minigestor.portal.theme";
export const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";
const DEFAULT_THEME_PREFERENCE: ThemePreference = "system";

export function toThemePreference(value: unknown): ThemePreference {
  return THEME_PREFERENCES.find((preference) => preference === value) ?? DEFAULT_THEME_PREFERENCE;
}

/**
 * Fija `data-theme` (el tema que se pinta) y `data-theme-preference` (lo elegido) en
 * `<html>`. Sin `chosen`, lee lo guardado. Tiene que bastarse sola, sin nada importado,
 * porque se incrusta como texto en `index.html`: por eso repite los nombres de los temas.
 */
export function applyThemePreference(storageKey: string, darkSchemeQuery: string, chosen?: string): void {
  let preference = chosen;
  if (preference === undefined) {
    try {
      preference = window.localStorage.getItem(storageKey) ?? undefined;
    } catch {
      preference = undefined;
    }
  }
  if (preference !== "light" && preference !== "dark") {
    preference = "system";
  }
  const systemTheme = window.matchMedia(darkSchemeQuery).matches ? "dark" : "light";
  const theme = preference === "system" ? systemTheme : preference;
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
}

/** El guion que corre antes de pintar, para que la página no parpadee en el otro tema. */
export function themeBootScript(): string {
  const parameters = [THEME_STORAGE_KEY, DARK_SCHEME_QUERY].map((value) => JSON.stringify(value));
  return `(${applyThemePreference.toString()})(${parameters.join(", ")});`;
}

/** Si el navegador bloquea el almacenamiento, la elección dura solo esta visita. */
export function rememberThemePreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    return;
  }
}
