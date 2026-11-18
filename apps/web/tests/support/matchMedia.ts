/** jsdom no trae `matchMedia`: uno que responde lo que el test decide y avisa al cambiar. */
export interface FakeMediaQuery {
  setMatches(matches: boolean): void;
}

export function installMatchMedia(initiallyMatches: boolean): FakeMediaQuery {
  let matches = initiallyMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const mediaQuery = {
    get matches() {
      return matches;
    },
    media: "",
    onchange: null,
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => true,
  };
  window.matchMedia = () => mediaQuery as unknown as MediaQueryList;
  return {
    setMatches(next) {
      matches = next;
      listeners.forEach((listener) => listener({ matches } as MediaQueryListEvent));
    },
  };
}
