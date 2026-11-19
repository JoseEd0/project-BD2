export interface Size {
  readonly width: number;
  readonly height: number;
}

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export type Pose = "front" | "left" | "right";
export type ScreenSide = "left" | "right";

/** Escala y desplazamiento de `object-fit: cover`: el cuadro llena la vista y se recorta. */
export function coverTransform(frame: Size, view: Size): { scale: number; offsetX: number; offsetY: number } {
  const scale = Math.max(view.width / frame.width, view.height / frame.height);
  return {
    scale,
    offsetX: (view.width - frame.width * scale) / 2,
    offsetY: (view.height - frame.height * scale) / 2,
  };
}

/**
 * Una caja en píxeles del cuadro enviado, llevada a la vista donde se dibuja el video con
 * `object-fit: cover`. La cámara frontal se muestra espejada, y la caja se espeja con ella:
 * el cuadro que viaja al servidor nunca va espejado.
 */
export function frameRectToView(rect: Rect, frame: Size, view: Size, mirrored: boolean): Rect {
  const { scale, offsetX, offsetY } = coverTransform(frame, view);
  const width = rect.width * scale;
  const x = rect.x * scale + offsetX;
  return {
    x: mirrored ? view.width - x - width : x,
    y: rect.y * scale + offsetY,
    width,
    height: rect.height * scale,
  };
}

/** El tamaño de un cuadro cuyo lado mayor no pasa de `maxSide`, sin ampliar nunca. */
export function fitWithin(source: Size, maxSide: number): Size {
  const factor = Math.min(1, maxSide / Math.max(source.width, source.height));
  return { width: Math.round(source.width * factor), height: Math.round(source.height * factor) };
}

export interface LabelPlacement {
  readonly x: number;
  readonly y: number;
  readonly below: boolean;
}

/**
 * Dónde va la placa de una caja: debajo, salvo que no quepa en la vista; centrada en la caja
 * y sin salirse por los lados.
 */
export function placeLabel(box: Rect, label: Size, view: Size, gap: number): LabelPlacement {
  const below = box.y + box.height + gap + label.height <= view.height;
  const y = below ? box.y + box.height + gap : Math.max(0, box.y - gap - label.height);
  const centered = box.x + box.width / 2 - label.width / 2;
  const x = Math.min(Math.max(0, centered), Math.max(0, view.width - label.width));
  return { x, y, below };
}

/**
 * Hacia qué lado de la pantalla apunta la flecha de una pose girada. «Izquierda» es la de la
 * persona: en la imagen sin espejar su cara se corre a la derecha, y en el espejo, a la
 * izquierda.
 */
export function poseArrowSide(pose: Pose, mirrored: boolean): ScreenSide | null {
  if (pose === "front") {
    return null;
  }
  const towardsImageRight = pose === "left";
  return towardsImageRight !== mirrored ? "right" : "left";
}
