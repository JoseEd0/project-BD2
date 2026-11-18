import { THEME_PREFERENCES, type ThemePreference } from "../theme/themePreference";
import { useThemePreference } from "../theme/useThemePreference";

const PREFERENCE_LABELS: Record<ThemePreference, string> = {
  system: "Sistema",
  light: "Claro",
  dark: "Oscuro",
};

/** Tres opciones de radio: con el teclado se recorren con las flechas, como cualquier grupo. */
export function ThemeSwitch() {
  const { preference, choosePreference } = useThemePreference();
  return (
    <fieldset className="theme-switch">
      <legend className="visually-hidden">Tema</legend>
      {THEME_PREFERENCES.map((option) => (
        <label key={option} className="theme-option">
          <input
            type="radio"
            name="theme"
            value={option}
            checked={preference === option}
            onChange={() => choosePreference(option)}
          />
          <span>{PREFERENCE_LABELS[option]}</span>
        </label>
      ))}
    </fieldset>
  );
}
