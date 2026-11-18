import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { installMatchMedia } from "./support/matchMedia";

installMatchMedia(false);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  installMatchMedia(false);
});
