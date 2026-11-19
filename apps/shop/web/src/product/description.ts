/** Lo que se muestra de la descripción: las viñetas de la ficha y los párrafos que siguen. */
export interface DescriptionParts {
  readonly bullets: readonly string[];
  readonly paragraphs: readonly string[];
}

const PARAGRAPH_BREAK = /\n{2,}/;
/** Así une las viñetas la carga del catálogo (`features` del dataset). */
const BULLET_SEPARATOR = " | ";

/** Parte `descripcion` (viñetas unidas por « | » y párrafos separados por una línea en blanco). */
export function descriptionParts(description: string | null | undefined): DescriptionParts {
  if (description === null || description === undefined || description.trim() === "") {
    return { bullets: [], paragraphs: [] };
  }
  const blocks = description
    .split(PARAGRAPH_BREAK)
    .map((block) => block.trim())
    .filter((block) => block !== "");
  const [first, ...rest] = blocks;
  if (first !== undefined && first.includes(BULLET_SEPARATOR)) {
    const bullets = first.split(BULLET_SEPARATOR).map((item) => item.trim()).filter((item) => item !== "");
    return { bullets, paragraphs: rest };
  }
  return { bullets: [], paragraphs: blocks };
}

/** La ruta de categorías sin la raíz del catálogo, que es la misma para todos. */
export function categoryTrail(path: string | null | undefined): string[] {
  if (!path) {
    return [];
  }
  const parts = path.split(">").map((part) => part.trim()).filter((part) => part !== "");
  return parts[0] === "Musical Instruments" ? parts.slice(1) : parts;
}
