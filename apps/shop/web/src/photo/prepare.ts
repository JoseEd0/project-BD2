import { UPLOAD_JPEG_QUALITY, UPLOAD_MAX_SIDE, UPLOAD_MAX_SOURCE_BYTES } from "../config";

/** Por qué una foto elegida no se puede usar, antes de subirla. */
export type PhotoRejection = "not-an-image" | "too-heavy" | "unreadable";

export const PHOTO_REJECTIONS: Readonly<Record<PhotoRejection, { readonly title: string; readonly detail: string }>> = {
  "not-an-image": { title: "Eso no es una foto", detail: "Elige una imagen JPEG, PNG o WebP." },
  "too-heavy": { title: "La foto pesa demasiado", detail: "Elige una de menos de 25 MB." },
  unreadable: {
    title: "No pudimos abrir la foto",
    detail: "Tu navegador no la pudo leer (pasa con algunas HEIC): prueba con un JPEG o una captura.",
  },
};

export class PhotoRejectedError extends Error {
  constructor(readonly reason: PhotoRejection) {
    super(PHOTO_REJECTIONS[reason].title);
    this.name = "PhotoRejectedError";
  }
}

/** Lo que se puede revisar sin abrir el archivo: que sea una imagen y lo que pesa. */
export function photoRejection(file: Pick<File, "type" | "size">): PhotoRejection | null {
  if (!file.type.startsWith("image/")) {
    return "not-an-image";
  }
  return file.size > UPLOAD_MAX_SOURCE_BYTES ? "too-heavy" : null;
}

/** El tamaño reducido que conserva la proporción, sin agrandar nunca. */
export function fittedSize(width: number, height: number, maxSide: number = UPLOAD_MAX_SIDE): { width: number; height: number } {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/**
 * La foto lista para subir: derecha según su EXIF y reducida a `UPLOAD_MAX_SIDE` px en JPEG,
 * así una foto de 6 MB del celular sube en unos 150 kB.
 *
 * @throws PhotoRejectedError si no es una imagen, pesa demasiado o el navegador no la abre.
 */
export async function preparePhoto(file: File): Promise<Blob> {
  const rejection = photoRejection(file);
  if (rejection !== null) {
    throw new PhotoRejectedError(rejection);
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new PhotoRejectedError("unreadable");
  }
  const size = fittedSize(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, size.width, size.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", UPLOAD_JPEG_QUALITY));
  if (blob === null) {
    throw new PhotoRejectedError("unreadable");
  }
  return blob;
}
