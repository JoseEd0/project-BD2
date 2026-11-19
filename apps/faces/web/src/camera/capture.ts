import { fitWithin, type Size } from "./geometry";

export interface JpegOptions {
  readonly maxSide: number;
  readonly quality: number;
}

export interface CapturedJpeg {
  readonly blob: Blob;
  readonly size: Size;
}

const JPEG_TYPE = "image/jpeg";

/**
 * El cuadro actual del video como JPEG reducido, sin espejar. `null` si el video todavía no
 * tiene imagen (recién conectado o detenido).
 */
export function captureJpeg(video: HTMLVideoElement, canvas: HTMLCanvasElement, options: JpegOptions): Promise<CapturedJpeg | null> {
  if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || video.videoWidth === 0) {
    return Promise.resolve(null);
  }
  const size = fitWithin({ width: video.videoWidth, height: video.videoHeight }, options.maxSide);
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext("2d");
  if (context === null) {
    return Promise.resolve(null);
  }
  context.drawImage(video, 0, 0, size.width, size.height);
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob === null ? null : { blob, size }), JPEG_TYPE, options.quality);
  });
}
