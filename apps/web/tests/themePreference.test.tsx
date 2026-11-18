import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeSwitch } from "../src/components/ThemeSwitch";
import {
  applyThemePreference,
  DARK_SCHEME_QUERY,
  rememberThemePreference,
  THEME_STORAGE_KEY,
  themeBootScript,
  toThemePreference,
} from "../src/theme/themePreference";
import { installMatchMedia } from "./support/matchMedia";

const root = document.documentElement;

function applied() {
  return { theme: root.dataset.theme, preference: root.dataset.themePreference, scheme: root.style.colorScheme };
}

beforeEach(() => {
  delete root.dataset.theme;
  delete root.dataset.themePreference;
});

describe("toThemePreference", () => {
  it("accepts the three preferences and falls back to the system", () => {
    expect(["system", "light", "dark"].map(toThemePreference)).toEqual(["system", "light", "dark"]);
    expect(toThemePreference("sepia")).toBe("system");
    expect(toThemePreference(undefined)).toBe("system");
  });
});

describe("applyThemePreference", () => {
  it("follows the system when nothing is stored", () => {
    installMatchMedia(true);
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    expect(applied()).toEqual({ theme: "dark", preference: "system", scheme: "dark" });
  });

  it("uses the stored choice over the system", () => {
    installMatchMedia(true);
    rememberThemePreference("light");
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    expect(applied()).toEqual({ theme: "light", preference: "light", scheme: "light" });
  });

  it("ignores a stored value it does not know", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    expect(applied()).toMatchObject({ theme: "light", preference: "system" });
  });

  it("still paints when the browser blocks storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    installMatchMedia(true);
    expect(() => rememberThemePreference("light")).not.toThrow();
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    expect(applied()).toMatchObject({ theme: "dark", preference: "system" });
    vi.restoreAllMocks();
  });
});

describe("themeBootScript", () => {
  it("is self-contained and applies the stored theme before React loads", () => {
    rememberThemePreference("dark");
    new Function(themeBootScript())();
    expect(applied()).toEqual({ theme: "dark", preference: "dark", scheme: "dark" });
  });
});

describe("ThemeSwitch", () => {
  it("starts from what the boot script applied", () => {
    root.dataset.themePreference = "dark";
    render(<ThemeSwitch />);
    expect(screen.getByRole("radio", { name: "Oscuro" })).toHaveProperty("checked", true);
  });

  it("applies and remembers a choice", async () => {
    const user = userEvent.setup();
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    render(<ThemeSwitch />);
    await user.click(screen.getByRole("radio", { name: "Oscuro" }));
    expect(screen.getByRole("radio", { name: "Oscuro" })).toHaveProperty("checked", true);
    expect(applied().theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("follows the system live only while the preference is the system", async () => {
    const user = userEvent.setup();
    const scheme = installMatchMedia(false);
    applyThemePreference(THEME_STORAGE_KEY, DARK_SCHEME_QUERY);
    render(<ThemeSwitch />);
    scheme.setMatches(true);
    expect(applied().theme).toBe("dark");
    await user.click(screen.getByRole("radio", { name: "Claro" }));
    scheme.setMatches(true);
    expect(applied().theme).toBe("light");
  });
});
