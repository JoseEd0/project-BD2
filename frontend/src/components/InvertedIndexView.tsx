import { formatBytes } from "../lib/bytes";
import type { InvertedStructure } from "../types";
import Stat from "./Stat";

const PERCENT = 100;

/** Qué guarda cada archivo del índice, por la clave con que lo describe el motor. */
const FILE_LABEL: Record<string, string> = {
  lexicon: "léxico: una entrada por término, en orden",
  terms: "texto de los términos",
  postings: "postings: (documento, frecuencia)",
  documents: "tabla de documentos: longitud y fila",
  document_map: "mapa de fila a documento",
};

function SizeTable({ inverted }: { inverted: InvertedStructure }) {
  const files = Object.entries(inverted.file_bytes);
  const total = files.reduce((sum, [, bytes]) => sum + bytes, 0);
  return (
    <div className="tablescroll">
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Archivo en disco</th>
            <th>Tamaño</th>
          </tr>
        </thead>
        <tbody>
          {files.map(([name, bytes]) => (
            <tr key={name}>
              <td>{FILE_LABEL[name] ?? name}</td>
              <td className="is-number">{formatBytes(bytes)}</td>
            </tr>
          ))}
          <tr>
            <td>
              <strong>total</strong>
            </td>
            <td className="is-number">
              <strong>{formatBytes(total)}</strong>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function SegmentTable({ inverted }: { inverted: InvertedStructure }) {
  const { pending } = inverted;
  return (
    <div className="tablescroll">
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Segmento</th>
            <th>Términos</th>
            <th>Postings</th>
            <th>Bloques SPIMI</th>
            <th>Pasadas de mezcla</th>
          </tr>
        </thead>
        <tbody>
          {inverted.segments.map((segment, index) => (
            <tr key={segment.generation}>
              <td>
                generación {segment.generation}
                {index === 0 ? " · principal" : ""}
              </td>
              <td className="is-number">{segment.terms.toLocaleString("es")}</td>
              <td className="is-number">{segment.postings.toLocaleString("es")}</td>
              <td className="is-number">{segment.blocks.toLocaleString("es")}</td>
              <td className="is-number">{segment.merge_passes.toLocaleString("es")}</td>
            </tr>
          ))}
          <tr>
            <td>en memoria · {pending.documents.toLocaleString("es")} documento(s)</td>
            <td className="is-number">{pending.terms.toLocaleString("es")}</td>
            <td className="is-number">{pending.postings.toLocaleString("es")}</td>
            <td colSpan={2}>~{formatBytes(pending.estimated_bytes)} sin escribir</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function TopTerms({ inverted }: { inverted: InvertedStructure }) {
  const documents = Math.max(inverted.documents, 1);
  return (
    <div className="tablescroll">
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Término (raíz)</th>
            <th>Documentos (df)</th>
            <th className="dfbar__header">Fracción de documentos</th>
            <th>Apariciones</th>
          </tr>
        </thead>
        <tbody>
          {inverted.top_terms.map((term) => {
            const share = (term.document_frequency / documents) * PERCENT;
            return (
              <tr key={term.term}>
                <td>
                  <code>{term.term}</code>
                </td>
                <td className="is-number">{term.document_frequency.toLocaleString("es")}</td>
                <td className="dfbar">
                  <span className="dfbar__fill" style={{ width: `${Math.min(share, PERCENT)}%` }} />
                  <span className="dfbar__value">{share.toFixed(1)} %</span>
                </td>
                <td className="is-number">{term.total_frequency.toLocaleString("es")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * La forma de un índice invertido: cuántos documentos y términos tiene, en qué segmentos de
 * SPIMI vive, cuánto ocupa cada archivo y qué términos aparecen en más documentos —los que
 * menos pesan en un ranking, porque su idf es el más bajo—.
 */
export default function InvertedIndexView({ inverted }: { inverted: InvertedStructure }) {
  return (
    <>
      <div className="stats">
        <Stat label="documentos" value={inverted.documents} />
        <Stat label="vocabulario (términos distintos)" value={inverted.vocabulary} />
        <Stat label="postings" value={inverted.postings} />
        <Stat label="términos por documento" value={inverted.average_length.toFixed(1)} />
        <Stat label="segmentos en disco" value={inverted.segments.length} />
        <Stat label="borrados con lápida" value={inverted.deleted} />
        <Stat label="borrados aún en postings" value={inverted.deleted_with_postings} />
        <Stat label="postings en disco" value={formatBytes(inverted.postings_bytes)} />
        <Stat label="fusiones" value={inverted.merges} />
        <Stat label="idioma" value={inverted.language} />
      </div>
      <SegmentTable inverted={inverted} />
      <TopTerms inverted={inverted} />
      <SizeTable inverted={inverted} />
    </>
  );
}
