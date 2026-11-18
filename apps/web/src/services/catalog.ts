/**
 * Los servicios que el portal conoce: el gestor, que es la plataforma, y las cuatro
 * aplicaciones de la Parte 5. Las rutas de montaje son las del gateway (epic #97): el
 * gestor en `/api` y cada aplicación en `/apps/<servicio>`.
 */

export type AppId = "copyright" | "shop" | "papers" | "faces";

export type ServiceId = "engine" | AppId;

export interface PortalService {
  readonly id: ServiceId;
  readonly name: string;
  readonly mountPath: string;
}

export interface PortalApp extends PortalService {
  readonly id: AppId;
  /** Ruta del portal, en español porque es lo que el usuario ve en la barra de direcciones. */
  readonly slug: string;
  readonly shortName: string;
  readonly summary: string;
  readonly capabilities: readonly string[];
}

export const ENGINE_MOUNT_PATH = "/api";
export const APPS_MOUNT_ROOT = "/apps";

/** Prefijos de todo lo que responde un servicio: nunca se guarda en el service worker. */
export const SERVICE_PATH_PREFIXES: readonly string[] = [ENGINE_MOUNT_PATH, APPS_MOUNT_ROOT];

export const ENGINE: PortalService = {
  id: "engine",
  name: "Gestor de base de datos",
  mountPath: ENGINE_MOUNT_PATH,
};

export const PORTAL_APPS: readonly PortalApp[] = [
  {
    id: "copyright",
    slug: "copyright",
    name: "Copyright en audio",
    shortName: "Copyright",
    mountPath: `${APPS_MOUNT_ROOT}/copyright`,
    summary: "Sube una canción y descubre qué tramos coinciden con canciones que ya existen.",
    capabilities: [
      "Compara la canción por fragmentos de pocos segundos contra todo el catálogo.",
      "Marca en la línea de tiempo cada tramo que coincide, con su porcentaje.",
      "Te deja escuchar tu tramo y el original desde el mismo punto.",
    ],
  },
  {
    id: "shop",
    slug: "tienda",
    name: "Tienda con recomendaciones",
    shortName: "Tienda",
    mountPath: `${APPS_MOUNT_ROOT}/shop`,
    summary: "Busca productos por texto o por foto y mira qué compraron otros clientes.",
    capabilities: [
      "Busca en el catálogo por descripción o con una foto del producto.",
      "Recomienda por compras reales, por texto y por parecido visual, y explica cada sugerencia.",
      "Muestra en el mapa las tiendas de Lima cercanas que lo tienen en stock.",
    ],
  },
  {
    id: "papers",
    slug: "papers",
    name: "Preguntas a papers",
    shortName: "Papers",
    mountPath: `${APPS_MOUNT_ROOT}/papers`,
    summary: "Pregunta sobre bases de datos y recuperación de información y recibe respuestas citadas.",
    capabilities: [
      "Responde con fragmentos de papers de arXiv y cita cada afirmación.",
      "Abre el PDF en la página exacta de cada cita.",
      "Filtra por año y categoría, y muestra qué fragmentos encontró cada búsqueda.",
    ],
  },
  {
    id: "faces",
    slug: "rostros",
    name: "Reconocimiento de rostros",
    shortName: "Rostros",
    mountPath: `${APPS_MOUNT_ROOT}/faces`,
    summary: "Regístrate con la cámara y comprueba al momento si el sistema te reconoce.",
    capabilities: [
      "Te guía para registrarte con tomas automáticas cuando la imagen es buena.",
      "Reconoce rostros en vivo sobre el video, con nombre y confianza.",
      "Borra tus datos desde la misma aplicación cuando quieras.",
    ],
  },
];

export function appById(id: AppId): PortalApp {
  const app = PORTAL_APPS.find((candidate) => candidate.id === id);
  if (app === undefined) {
    throw new Error(`aplicación desconocida: ${id}`);
  }
  return app;
}

/** La URL base de un servicio bajo `apiBaseUrl`, sin barras dobles entre las dos partes. */
export function serviceBaseUrl(apiBaseUrl: string, service: PortalService): string {
  return apiBaseUrl.replace(/\/+$/, "") + service.mountPath;
}
