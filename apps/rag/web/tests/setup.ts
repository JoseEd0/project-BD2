import { installMatchMedia } from "@minigestor/web-kit/testing";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

installMatchMedia(false);
// jsdom no hace layout: no hay a dónde desplazarse.
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  installMatchMedia(false);
});
