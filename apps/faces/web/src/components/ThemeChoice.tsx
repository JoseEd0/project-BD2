import { THEME_PREFERENCES, useThemePreference, type ThemePreference } from "@minigestor/web-kit";

const PREFERENCE_LABELS: Record<ThemePreference, string> = {
  system: "Sistema",
  light: "Claro",
  dark: "Oscuro",
};

/** Tres opciones de radio: con el teclado se recorren con las flechas, como cualquier grupo. */
export function ThemeChoice() {
  const { preference, choosePreference } = useThemePreference();
  return (
    <fieldset className="theme-choice">
      <legend>Tema de las pantallas de texto</legend>
      <div className="segmented">
        {THEME_PREFERENCES.map((option) => (
          <label key={option}>
            <input
              type="radio"
              name="theme"
              value={option}
              checked={preference === option}
              onChange={() => choosePreference(option)}
            />
            {PREFERENCE_LABELS[option]}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
