import { THEME_PREFERENCES, useThemePreference, type ThemePreference } from "@minigestor/web-kit";

const PREFERENCE_LABELS: Record<ThemePreference, string> = {
  system: "Auto",
  light: "Claro",
  dark: "Oscuro",
};

/** Tres opciones de radio: con el teclado se recorren con las flechas, como cualquier grupo. */
export function ThemeChoice() {
  const { preference, choosePreference } = useThemePreference();
  return (
    <fieldset className="segmented segmented--on-edition">
      <legend className="visually-hidden">Tema</legend>
      {THEME_PREFERENCES.map((option) => (
        <label key={option} className="segmented__option">
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
