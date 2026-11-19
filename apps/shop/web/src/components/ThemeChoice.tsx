import { THEME_PREFERENCES, useThemePreference, type ThemePreference } from "@minigestor/web-kit";

import { Icon, type IconName } from "./Icon";

const PREFERENCES: Record<ThemePreference, { readonly label: string; readonly icon: IconName }> = {
  system: { label: "Tema del sistema", icon: "monitor" },
  light: { label: "Tema claro", icon: "sun" },
  dark: { label: "Tema oscuro", icon: "moon" },
};

/** Tres radios: con el teclado se recorren con las flechas, como cualquier grupo. */
export function ThemeChoice() {
  const { preference, choosePreference } = useThemePreference();
  return (
    <fieldset className="theme">
      <legend className="visually-hidden">Tema</legend>
      {THEME_PREFERENCES.map((option) => (
        <label key={option} className="theme-option" title={PREFERENCES[option].label}>
          <input
            type="radio"
            name="theme"
            value={option}
            checked={preference === option}
            onChange={() => choosePreference(option)}
          />
          <Icon name={PREFERENCES[option].icon} size={18} />
          <span className="visually-hidden">{PREFERENCES[option].label}</span>
        </label>
      ))}
    </fieldset>
  );
}
