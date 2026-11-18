import { useCallback, useEffect, useState } from "react";

import {
  applyThemePreference,
  DARK_SCHEME_QUERY,
  rememberThemePreference,
  THEME_STORAGE_KEY,
  toThemePreference,
  type ThemePreference,
} from "./themePreference";

function appliedPreference(): ThemePreference {
  return toThemePreference(document.documentElement.dataset.themePreference);
}

/** La preferencia de tema, recordada entre visitas; con «sistema», sigue sus cambios. */
export function useThemePreference(): {
  preference: ThemePreference;
  choosePreference: (preference: ThemePreference) => void;
} {
  const [preference, setPreference] = useState<ThemePreference>(appliedPreference);

  useEffect(() => {
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY, preference);
    if (preference !== "system") {
      return undefined;
    }
    const scheme = window.matchMedia(DARK_SCHEME_QUERY);
    const followSystem = (): void => applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY, preference);
    scheme.addEventListener("change", followSystem);
    return () => scheme.removeEventListener("change", followSystem);
  }, [preference]);

  const choosePreference = useCallback((next: ThemePreference) => {
    rememberThemePreference(next);
    setPreference(next);
  }, []);

  return { preference, choosePreference };
}
