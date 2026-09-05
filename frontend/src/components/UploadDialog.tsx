import { useState } from "react";

export interface UploadValues {
  file: File;
  name: string;
  organization: string;
  keyColumn: string | null;
}

interface UploadDialogProps {
  busy: boolean;
  onSubmit: (values: UploadValues) => void;
  onClose: () => void;
}

const ORGANIZATIONS = [
  { value: "heap", label: "Heap file", hint: "Inserción rápida; se consulta por índice." },
  {
    value: "clustered_btree",
    label: "B+ agrupado",
    hint: "Las filas viven en el árbol, ordenadas por la clave.",
  },
  {
    value: "sequential",
    label: "Archivo secuencial",
    hint: "Ordenado por la clave, con zona de desbordamiento.",
  },
];

const CSV_EXTENSION = ".csv";

function tableNameFrom(file: File): string {
  return file.name
    .replace(/\.[^.]+$/, "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9_]/g, "_")
    .replace(/^([^A-Za-z_])/, "t$1");
}

export default function UploadDialog({ busy, onSubmit, onClose }: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [organization, setOrganization] = useState(ORGANIZATIONS[0].value);
  const [keyColumn, setKeyColumn] = useState("id");

  const needsKey = organization !== "heap";
  const ready = file !== null && name.trim().length > 0 && (!needsKey || keyColumn.trim().length > 0);

  function pick(chosen: File | null) {
    setFile(chosen);
    if (chosen !== null && name.trim().length === 0) {
      setName(tableNameFrom(chosen));
    }
  }

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div className="modal__panel" onClick={(event) => event.stopPropagation()} role="dialog">
        <header className="modal__header">
          <h2>Cargar CSV como tabla</h2>
          <button className="icon-button" onClick={onClose} type="button">
            ✕
          </button>
        </header>

        <div className="modal__body">
          <label className="field">
            <span className="field__label">Archivo CSV (con cabecera)</span>
            <input
              accept={CSV_EXTENSION}
              className="field__input"
              onChange={(event) => pick(event.target.files?.[0] ?? null)}
              type="file"
            />
            {file !== null && (
              <span className="field__hint">
                {file.name} · {(file.size / 1024).toFixed(1)} KiB
              </span>
            )}
          </label>

          <label className="field">
            <span className="field__label">Nombre de la tabla</span>
            <input
              className="field__input"
              onChange={(event) => setName(event.target.value)}
              placeholder="productos"
              value={name}
            />
          </label>

          <fieldset className="field">
            <legend className="field__label">Organización física</legend>
            <div className="choices">
              {ORGANIZATIONS.map((option) => (
                <label
                  className={organization === option.value ? "choice choice--active" : "choice"}
                  key={option.value}
                >
                  <input
                    checked={organization === option.value}
                    name="organization"
                    onChange={() => setOrganization(option.value)}
                    type="radio"
                    value={option.value}
                  />
                  <span className="choice__label">{option.label}</span>
                  <span className="choice__hint">{option.hint}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {needsKey && (
            <label className="field">
              <span className="field__label">Columna clave</span>
              <input
                className="field__input"
                onChange={(event) => setKeyColumn(event.target.value)}
                placeholder="id"
                value={keyColumn}
              />
              <span className="field__hint">
                Debe existir en la cabecera del CSV; es por la que se ordenará la tabla.
              </span>
            </label>
          )}

          <p className="modal__note">
            Los tipos de cada columna se deducen leyendo el archivo entero: entero, real,
            fecha <code>AAAA-MM-DD</code> o texto.
          </p>
        </div>

        <footer className="modal__footer">
          <button className="action" onClick={onClose} type="button">
            Cancelar
          </button>
          <button
            className="action action--primary"
            disabled={!ready || busy}
            onClick={() =>
              file !== null &&
              onSubmit({
                file,
                name: name.trim(),
                organization,
                keyColumn: needsKey ? keyColumn.trim() : null,
              })
            }
            type="button"
          >
            {busy ? "Cargando…" : "Crear tabla"}
          </button>
        </footer>
      </div>
    </div>
  );
}
