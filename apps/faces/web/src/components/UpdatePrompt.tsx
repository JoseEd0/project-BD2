import { useRegisterSW } from "virtual:pwa-register/react";

/** Registra el service worker y avisa cuando hay una versión nueva; se aplica cuando la
 * persona lo pide, no a mitad de un registro. */
export function UpdatePrompt() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();
  if (!needRefresh) {
    return null;
  }
  return (
    <div className="toast" role="status">
      <span>Hay una versión nueva.</span>
      <button type="button" className="button" onClick={() => void updateServiceWorker()}>
        Actualizar
      </button>
    </div>
  );
}
