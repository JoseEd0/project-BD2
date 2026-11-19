import { useRegisterSW } from "virtual:pwa-register/react";

/** Registra el service worker y avisa cuando hay una versión nueva; se aplica cuando el
 * usuario lo pide, no a mitad de lo que esté haciendo. */
export function UpdatePrompt() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();
  if (!needRefresh) {
    return null;
  }
  return (
    <div role="status">
      <span>Hay una versión nueva.</span>
      <button type="button" onClick={() => void updateServiceWorker()}>
        Actualizar
      </button>
    </div>
  );
}
