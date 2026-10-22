import Modal from "./Modal";

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
    <Modal
      footer={
        <>
          <button className="action" onClick={onClose} type="button">
            Cancelar
          </button>
          <button className="action action--danger" onClick={onConfirm} type="button">
            {confirmLabel}
          </button>
        </>
      }
      onClose={onClose}
      title={title}
      width="narrow"
    >
      <div className="modal__body">
        <p className="modal__message">{message}</p>
        {detail && <p className="modal__note">{detail}</p>}
      </div>
    </Modal>
  );
}
