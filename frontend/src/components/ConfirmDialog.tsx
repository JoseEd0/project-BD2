interface ConfirmDialogProps {
  title: string;
  message: string;
  detail?: string;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
}

export default function ConfirmDialog({
  title,
  message,
  detail,
  confirmLabel,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div
        className="modal__panel modal__panel--narrow"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="modal__header">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} type="button">
            ✕
          </button>
        </header>
        <div className="modal__body">
          <p className="modal__message">{message}</p>
          {detail && <p className="modal__note">{detail}</p>}
        </div>
        <footer className="modal__footer">
          <button className="action" onClick={onClose} type="button">
            Cancelar
          </button>
          <button className="action action--danger" onClick={onConfirm} type="button">
            {confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}
