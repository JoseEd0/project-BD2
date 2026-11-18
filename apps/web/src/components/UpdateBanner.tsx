import { useRegisterSW } from "virtual:pwa-register/react";

/** Avisa cuando hay una versión nueva del portal; se aplica cuando el usuario lo pide, no a
 * mitad de lo que esté haciendo. */
export function UpdateBanner() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();
  if (!needRefresh) {
    return null;
  }
  return (
    <div className="notice" role="status">
      <span>Hay una versión nueva del portal.</span>
      <button type="button" className="button" onClick={() => void updateServiceWorker()}>
        Actualizar
      </button>
    </div>
  );
}
