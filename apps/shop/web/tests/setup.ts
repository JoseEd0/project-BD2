import { installMatchMedia } from "@minigestor/web-kit/testing";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

installMatchMedia(false);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  installMatchMedia(false);
});
