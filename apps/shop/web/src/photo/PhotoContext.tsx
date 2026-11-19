import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { PhotoRejectedError, preparePhoto, type PhotoRejection } from "./prepare";

export interface ChosenPhoto {
  readonly blob: Blob;
  /** Una URL `blob:` para mostrarla sin volver a la red. */
  readonly url: string;
}

interface PhotoState {
  readonly photo: ChosenPhoto | null;
  readonly preparing: boolean;
  readonly rejection: PhotoRejection | null;
  /** Prepara la foto elegida; devuelve si quedó lista para buscar. */
  readonly choose: (file: File) => Promise<boolean>;
  readonly clear: () => void;
}

const PhotoContext = createContext<PhotoState | null>(null);

/** La foto con que se busca: elegida en la barra, buscada en el catálogo. */
export function PhotoProvider({ children }: { readonly children: ReactNode }) {
  const [photo, setPhoto] = useState<ChosenPhoto | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [rejection, setRejection] = useState<PhotoRejection | null>(null);

  useEffect(() => () => {
    if (photo !== null) {
      URL.revokeObjectURL(photo.url);
    }
  }, [photo]);

  const choose = useCallback(async (file: File) => {
    setPreparing(true);
    setRejection(null);
    try {
      const blob = await preparePhoto(file);
      setPhoto({ blob, url: URL.createObjectURL(blob) });
      return true;
    } catch (error) {
      setRejection(error instanceof PhotoRejectedError ? error.reason : "unreadable");
      return false;
    } finally {
      setPreparing(false);
    }
  }, []);

  const clear = useCallback(() => {
    setPhoto(null);
    setRejection(null);
  }, []);

  const value = useMemo(() => ({ photo, preparing, rejection, choose, clear }), [photo, preparing, rejection, choose, clear]);
  return <PhotoContext.Provider value={value}>{children}</PhotoContext.Provider>;
}

export function usePhoto(): PhotoState {
  const state = useContext(PhotoContext);
  if (state === null) {
    throw new Error("usePhoto necesita un PhotoProvider");
  }
  return state;
}
