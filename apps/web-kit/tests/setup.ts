import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { installMatchMedia } from "../src/testing/index.ts";

installMatchMedia(false);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  installMatchMedia(false);
});
