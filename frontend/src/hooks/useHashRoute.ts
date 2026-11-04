import { useSyncExternalStore } from "react";

import { pageFromHash } from "../routes";
import type { PageId } from "../routes";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function currentPage(): PageId {
  return pageFromHash(window.location.hash);
}

/** La página que pide la dirección, al día con cada cambio del `#`. */
export function useHashRoute(): PageId {
  return useSyncExternalStore(subscribe, currentPage);
}
