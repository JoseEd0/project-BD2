import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

/**
 * Clave de `localStorage` con la preferencia. El arranque de `index.html` lee esta misma
 * clave antes de pintar nada, para que la página no parpadee en el tema contrario.
 */
const STORAGE_KEY = "minigestor.theme";

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/** Si el navegador bloquea el almacenamiento, el tema elegido dura solo esta visita. */
function rememberTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    return;
  }
}

/** Tema de la interfaz, con el cambio guardado para la próxima visita. */
export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = useCallback(() => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    rememberTheme(next);
    setTheme(next);
  }, []);

  return { theme, toggleTheme };
}
