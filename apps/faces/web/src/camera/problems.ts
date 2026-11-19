/**
 * Por qué no hay cámara, y cómo se arregla en cada navegador.
 *
 * - `insecure`: la página no se abrió por HTTPS, y el navegador no ofrece la cámara.
 * - `unsupported`: el navegador no tiene `getUserMedia` (uno muy viejo o integrado en otra app).
 * - `denied`: la persona o el sistema negaron el permiso.
 * - `not-found`: el equipo no tiene cámara.
 * - `in-use`: otra app la tiene tomada o el sistema no la pudo abrir.
 * - `unknown`: cualquier otro fallo.
 */
export type CameraProblem = "insecure" | "unsupported" | "denied" | "not-found" | "in-use" | "unknown";

export type Browser = "ios" | "android-chrome" | "samsung" | "firefox" | "desktop-safari" | "desktop-chrome" | "other";

const PROBLEM_BY_ERROR_NAME: Record<string, CameraProblem> = {
  NotAllowedError: "denied",
  PermissionDeniedError: "denied",
  SecurityError: "denied",
  NotFoundError: "not-found",
  DevicesNotFoundError: "not-found",
  OverconstrainedError: "not-found",
  NotReadableError: "in-use",
  TrackStartError: "in-use",
  AbortError: "in-use",
};

export function cameraProblemOf(error: unknown): CameraProblem {
  const name = typeof error === "object" && error !== null && "name" in error ? String(error.name) : "";
  return PROBLEM_BY_ERROR_NAME[name] ?? "unknown";
}

export interface CameraEnvironment {
  readonly isSecureContext: boolean;
  readonly mediaDevices: MediaDevices | undefined;
}

/** Lo que se sabe antes de pedir nada: sin HTTPS no hay `mediaDevices` en ningún navegador. */
export function cameraSupportProblem(environment: CameraEnvironment): CameraProblem | null {
  if (!environment.isSecureContext) {
    return "insecure";
  }
  if (typeof environment.mediaDevices?.getUserMedia !== "function") {
    return "unsupported";
  }
  return null;
}

export function browserOf(userAgent: string): Browser {
  if (/iPhone|iPad|iPod/.test(userAgent)) {
    return "ios";
  }
  if (/SamsungBrowser/.test(userAgent)) {
    return "samsung";
  }
  if (/Firefox|FxiOS/.test(userAgent)) {
    return "firefox";
  }
  if (/Android/.test(userAgent)) {
    return "android-chrome";
  }
  if (/Chrome|Chromium|Edg\//.test(userAgent)) {
    return "desktop-chrome";
  }
  if (/Safari/.test(userAgent)) {
    return "desktop-safari";
  }
  return "other";
}

const PERMISSION_STEPS: Record<Browser, readonly string[]> = {
  ios: [
    "Abre Ajustes › Safari › Cámara y elige «Preguntar» o «Permitir».",
    "O en Safari, toca «aA» en la barra de direcciones › Configuración del sitio web › Cámara › Permitir.",
    "Vuelve aquí y toca «Intentar de nuevo».",
  ],
  "android-chrome": [
    "Toca el ícono a la izquierda de la dirección › Permisos › Cámara › Permitir.",
    "Si no aparece, en Ajustes › Apps › Chrome › Permisos › Cámara, elige «Permitir».",
    "Vuelve aquí y toca «Intentar de nuevo».",
  ],
  samsung: [
    "Toca el candado de la barra de direcciones › Permisos › Cámara › Permitir.",
    "Si no aparece, en Ajustes › Aplicaciones › Samsung Internet › Permisos › Cámara.",
    "Vuelve aquí y toca «Intentar de nuevo».",
  ],
  firefox: [
    "Toca el candado de la barra de direcciones y quita el bloqueo de la cámara.",
    "Vuelve aquí y toca «Intentar de nuevo»: Firefox preguntará otra vez.",
  ],
  "desktop-safari": [
    "En Safari › Configuración › Sitios web › Cámara, elige «Permitir» para este sitio.",
    "Vuelve aquí y pulsa «Intentar de nuevo».",
  ],
  "desktop-chrome": [
    "Pulsa el ícono a la izquierda de la dirección › Cámara › Permitir.",
    "Vuelve aquí y pulsa «Intentar de nuevo».",
  ],
  other: [
    "Busca los permisos del sitio en el menú del navegador y permite la cámara.",
    "Vuelve aquí y toca «Intentar de nuevo».",
  ],
};

export function permissionSteps(browser: Browser): readonly string[] {
  return PERMISSION_STEPS[browser];
}

/**
 * La misma página por HTTPS cuando se abrió por HTTP en el puerto de la web del compose; si
 * no, `null` (la dirección segura la conoce quien la compartió: el túnel, por ejemplo).
 */
export function secureAddressFor(location: Pick<Location, "protocol" | "hostname" | "port" | "pathname">, portPairs: Readonly<Record<string, string>>): string | null {
  if (location.protocol === "https:") {
    return null;
  }
  const securePort = portPairs[location.port];
  if (securePort === undefined) {
    return null;
  }
  return `https://${location.hostname}:${securePort}${location.pathname}`;
}
