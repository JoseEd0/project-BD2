import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { cameraProblemOf, cameraSupportProblem, type CameraProblem } from "./problems";

export type Facing = "user" | "environment";

export type CameraStatus =
  | { readonly kind: "starting" }
  | { readonly kind: "live" }
  | { readonly kind: "hidden" }
  | { readonly kind: "problem"; readonly problem: CameraProblem };

export interface Camera {
  readonly status: CameraStatus;
  readonly facing: Facing;
  /** La frontal se muestra como un espejo. */
  readonly mirrored: boolean;
  readonly canFlip: boolean;
  flip(): void;
  retry(): void;
}

/** Lo bastante para que una toma tenga detalle; el navegador da lo más cercano que pueda. */
const IDEAL_WIDTH = 1280;
const IDEAL_HEIGHT = 720;

function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

async function countCameras(): Promise<number> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((device) => device.kind === "videoinput").length;
  } catch {
    return 0;
  }
}

/**
 * La cámara en `video`: la pide al montarse, la suelta al desmontarse y **la apaga mientras la
 * pestaña está oculta** (la luz de la cámara se apaga), para volver a pedirla al mostrarse.
 */
export function useCamera(video: RefObject<HTMLVideoElement | null>, initialFacing: Facing = "user"): Camera {
  const [facing, setFacing] = useState<Facing>(initialFacing);
  const [status, setStatus] = useState<CameraStatus>({ kind: "starting" });
  const [canFlip, setCanFlip] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");
  const stream = useRef<MediaStream | null>(null);

  useEffect(() => {
    const onVisibility = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (hidden) {
      setStatus({ kind: "hidden" });
      return;
    }
    const unsupported = cameraSupportProblem({ isSecureContext: window.isSecureContext, mediaDevices: navigator.mediaDevices });
    if (unsupported !== null) {
      setStatus({ kind: "problem", problem: unsupported });
      return;
    }
    let cancelled = false;
    setStatus({ kind: "starting" });
    navigator.mediaDevices
      .getUserMedia({ audio: false, video: { facingMode: { ideal: facing }, width: { ideal: IDEAL_WIDTH }, height: { ideal: IDEAL_HEIGHT } } })
      .then(async (opened) => {
        if (cancelled) {
          stopStream(opened);
          return;
        }
        stream.current = opened;
        const element = video.current;
        if (element !== null) {
          element.srcObject = opened;
          await element.play().catch(() => undefined);
        }
        setCanFlip((await countCameras()) > 1);
        if (!cancelled) {
          setStatus({ kind: "live" });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setStatus({ kind: "problem", problem: cameraProblemOf(error) });
        }
      });
    return () => {
      cancelled = true;
      stopStream(stream.current);
      stream.current = null;
      if (video.current !== null) {
        video.current.srcObject = null;
      }
    };
  }, [facing, hidden, attempt, video]);

  const flip = useCallback(() => setFacing((current) => (current === "user" ? "environment" : "user")), []);
  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  return { status, facing, mirrored: facing === "user", canFlip, flip, retry };
}
