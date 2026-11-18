import { useOnlineStatus } from "../hooks/useOnlineStatus";

export function OfflineNotice() {
  if (useOnlineStatus()) {
    return null;
  }
  return (
    <div className="notice" role="status">
      Sin conexión. El portal abre igual, pero las aplicaciones necesitan red para responder.
    </div>
  );
}
