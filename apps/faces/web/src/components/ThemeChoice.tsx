import { THEME_PREFERENCES, useThemePreference, type ThemePreference } from "@minigestor/web-kit";

import { Icon, type IconName } from "./Icon";

const PREFERENCES: Record<ThemePreference, { readonly label: string; readonly icon: IconName }> = {
  system: { label: "Tema del sistema", icon: "themeAuto" },
  light: { label: "Claro", icon: "sun" },
  dark: { label: "Oscuro", icon: "moon" },
};

/**
 * El tema de las pantallas de texto, en la barra de cada una: tres opciones de radio con
 * ícono, que con el teclado se recorren con las flechas como cualquier grupo.
 */
export function ThemeChoice() {
  const { preference, choosePreference } = useThemePreference();
  return (
    <fieldset className="theme-choice">
      <legend className="visually-hidden">Tema de las pantallas de texto</legend>
      {THEME_PREFERENCES.map((option) => (
        <label key={option} title={PREFERENCES[option].label}>
          <input
            type="radio"
            name="theme"
            value={option}
            aria-label={PREFERENCES[option].label}
            checked={preference === option}
            onChange={() => choosePreference(option)}
          />
          <Icon name={PREFERENCES[option].icon} size={20} />
        </label>
      ))}
    </fieldset>
  );
}
