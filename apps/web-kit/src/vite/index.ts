import type { PluginOption } from "vite";

import { kitResolution } from "./kitResolution.ts";
import { themeBoot } from "./themeBoot.ts";

export { kitResolution, SHARED_DEPENDENCIES } from "./kitResolution.ts";
export { inlineScriptHash, themeBoot, themeBootScriptHash } from "./themeBoot.ts";

/** Lo que toda interfaz que usa el kit añade a sus plugins de Vite. */
export function webKit(): PluginOption[] {
  return [kitResolution(), themeBoot()];
}
