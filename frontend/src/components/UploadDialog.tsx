import { useEffect, useState } from "react";
import type { DragEvent } from "react";

import { readCsvHeader, tableNameFrom } from "../lib/csvFiles";

export type UploadValues =
  | { mode: "create"; file: File; name: string; organization: string; keyColumn: string | null }
  | { mode: "fileOnly"; file: File; name: string };

interface UploadDialogProps {
  busy: boolean;
  onSubmit: (values: UploadValues) => void;
  onClose: () => void;
}

type Mode = UploadValues["mode"];

const ORGANIZATIONS = [
  {
    value: "heap",
    label: "Heap file",
    hint: "Filas en orden de llegada; se consulta por índice.",
    keyRequired: false,
  },
  {
    value: "clustered_btree",
    label: "B+ agrupado",
    hint: "Las filas viven en las hojas del árbol, ordenadas por la clave.",
    keyRequired: true,
  },
  {
    value: "sequential",
    label: "Archivo secuencial",
    hint: "Ordenado por la clave, con zona de desbordamiento y reorganización.",
    keyRequired: true,
  },
];

const CSV_EXTENSION = ".csv";
const BYTES_PER_KIB = 1024;
const PREFERRED_KEY = "id";
const NO_KEY = "";

function defaultKey(columns: string[], keyRequired: boolean): string {
  if (columns.includes(PREFERRED_KEY)) return PREFERRED_KEY;
  return keyRequired ? (columns[0] ?? NO_KEY) : NO_KEY;
}

export default function UploadDialog({ busy, onSubmit, onClose }: UploadDialogProps) {
  const [mode, setMode] = useState<Mode>("create");
  const [file, setFile] = useState<File | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [organization, setOrganization] = useState(ORGANIZATIONS[0].value);
  const [keyColumn, setKeyColumn] = useState(NO_KEY);
  const [dragging, setDragging] = useState(false);

  const chosen = ORGANIZATIONS.find((option) => option.value === organization) ?? ORGANIZATIONS[0];

  useEffect(() => {
    setKeyColumn(defaultKey(columns, chosen.keyRequired));
  }, [columns, chosen.keyRequired]);

  const keyMissing = mode === "create" && chosen.keyRequired && keyColumn === NO_KEY;
  const ready = file !== null && name.trim().length > 0 && !keyMissing;

  async function pick(candidate: File | null) {
    setFile(candidate);
    setColumns(candidate === null ? [] : await readCsvHeader(candidate));
    if (candidate !== null) {
      setName(tableNameFrom(candidate));
    }
  }

  function drop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    void pick(event.dataTransfer.files[0] ?? null);
  }

  function submit() {
    if (file === null) return;
    if (mode === "fileOnly") {
      onSubmit({ mode, file, name: name.trim() });
      return;
    }
    onSubmit({
      mode,
      file,
      name: name.trim(),
      organization,
      keyColumn: keyColumn === NO_KEY ? null : keyColumn,
    });
  }

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div className="modal__panel" onClick={(event) => event.stopPropagation()} role="dialog">
        <header className="modal__header">
          <h2>Cargar CSV</h2>
          <button className="icon-button" onClick={onClose} type="button">
            ✕
          </button>
        </header>

        <div className="modal__body">
          <label
            className={dragging ? "dropzone dropzone--active" : "dropzone"}
            onDragLeave={() => setDragging(false)}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDrop={drop}
          >
            <input
              accept={CSV_EXTENSION}
              className="dropzone__input"
              onChange={(event) => void pick(event.target.files?.[0] ?? null)}
              type="file"
            />
            {file === null ? (
              <>
                <span className="dropzone__title">Arrastra un CSV aquí</span>
                <span className="dropzone__hint">o haz clic para elegirlo · debe tener cabecera</span>
              </>
            ) : (
              <>
                <span className="dropzone__title">{file.name}</span>
                <span className="dropzone__hint">
                  {(file.size / BYTES_PER_KIB).toFixed(1)} KiB · {columns.length} columnas:{" "}
                  {columns.join(", ")}
                </span>
              </>
            )}
          </label>

          <div className="segmented" role="tablist">
            <button
              aria-selected={mode === "create"}
              className={mode === "create" ? "segmented__item segmented__item--active" : "segmented__item"}
              onClick={() => setMode("create")}
              role="tab"
              type="button"
            >
              Crear la tabla ahora
            </button>
            <button
              aria-selected={mode === "fileOnly"}
              className={mode === "fileOnly" ? "segmented__item segmented__item--active" : "segmented__item"}
              onClick={() => setMode("fileOnly")}
              role="tab"
              type="button"
            >
              Solo subir el archivo
            </button>
          </div>

          <label className="field">
            <span className="field__label">
              {mode === "create" ? "Nombre de la tabla" : "Nombre del archivo en el servidor"}
            </span>
            <input
              className="field__input"
              onChange={(event) => setName(event.target.value)}
              placeholder="productos"
              value={name}
            />
          </label>

          {mode === "fileOnly" ? (
            <p className="modal__note">
              El archivo se guarda en el servidor <strong>sin crear ninguna tabla</strong>. El
              editor recibe un <code>CREATE TABLE … FROM FILE</code> listo para editar: ahí
              eliges la organización y la clave con SQL.
            </p>
          ) : (
            <>
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

              <label className="field">
                <span className="field__label">Clave primaria</span>
                <select
                  className="field__input"
                  disabled={columns.length === 0}
                  onChange={(event) => setKeyColumn(event.target.value)}
                  value={keyColumn}
                >
                  {columns.length === 0 && <option value={NO_KEY}>Elige primero un archivo</option>}
                  {columns.length > 0 && !chosen.keyRequired && (
                    <option value={NO_KEY}>Sin clave primaria</option>
                  )}
                  {columns.map((column) => (
                    <option key={column} value={column}>
                      {column}
                    </option>
                  ))}
                </select>
                <span className="field__hint">
                  {chosen.keyRequired
                    ? "Obligatoria: la estructura ordena las filas por ella."
                    : keyColumn === NO_KEY
                      ? "Sin clave: las filas solo se identifican por su dirección (página, ranura) y nada impide valores repetidos. Buscar por id recorre la tabla hasta que le crees un índice."
                      : "La clave recibe un índice hash y no admite repetidos."}
                </span>
              </label>
            </>
          )}
        </div>

        <footer className="modal__footer">
          <button className="action" onClick={onClose} type="button">
            Cancelar
          </button>
          <button
            className="action action--primary"
            disabled={!ready || busy}
            onClick={submit}
            type="button"
          >
            {busy ? "Subiendo…" : mode === "create" ? "Crear tabla" : "Subir archivo"}
          </button>
        </footer>
      </div>
    </div>
  );
}
