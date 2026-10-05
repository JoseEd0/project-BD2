import { useEffect, useState } from "react";

import { ApiError, fetchStructure } from "../api";
import { averageFill } from "../lib/spatial";
import type {
  Bounds,
  HashStructure,
  HeapStorage,
  IndexStructure,
  RTreeStructure,
  SequentialStorage,
  TableInfo,
  TableStructure,
  TreeStructure,
} from "../types";

interface StructureDialogProps {
  table: TableInfo;
  onClose: () => void;
}

const PERCENT = 100;
/** Lado mayor del dibujo de los MBR, en unidades del SVG. */
const DRAWING_SIZE = 1000;
const DEGREES_TO_RADIANS = Math.PI / 180;

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span className="stat__value">
        {typeof value === "number" ? value.toLocaleString("es") : value}
      </span>
      <span className="stat__label">{label}</span>
    </div>
  );
}

function TreeView({ tree }: { tree: TreeStructure }) {
  return (
    <>
      <div className="stats">
        <Stat label="altura (niveles)" value={tree.height} />
        <Stat label="entradas" value={tree.entries} />
        <Stat label="entradas por hoja" value={tree.leaf_capacity} />
        <Stat label="separadores por nodo interno" value={tree.internal_capacity} />
        <Stat label="páginas del archivo" value={tree.pages} />
      </div>
      <div className="tree">
        {tree.levels.map((level, depth) => (
          <div className="tree__level" key={depth}>
            <div className="tree__caption">
              Nivel {depth} · {depth === 0 ? "raíz" : level.kind} ·{" "}
              {level.node_count.toLocaleString("es")} nodo(s) ·{" "}
              {level.key_count.toLocaleString("es")} claves
            </div>
            <div className="tree__nodes">
              {level.nodes.map((node, index) => (
                <div
                  className={level.kind === "hojas" ? "tree__node tree__node--leaf" : "tree__node"}
                  key={index}
                  title={`${node.key_count} claves`}
                >
                  {node.keys.join(" · ")}
                  {node.key_count > node.keys.length && " · …"}
                  <span className="tree__count">{node.key_count}</span>
                </div>
              ))}
              {level.node_count > level.nodes.length && (
                <span className="tree__more">
                  +{(level.node_count - level.nodes.length).toLocaleString("es")} nodos más
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function HashView({ hash }: { hash: HashStructure }) {
  return (
    <>
      <div className="stats">
        <Stat label="profundidad global" value={hash.global_depth} />
        <Stat label="entradas del directorio (2^gd)" value={hash.directory_size} />
        <Stat label="cubetas distintas" value={hash.bucket_count} />
        <Stat label="capacidad de cubeta" value={hash.bucket_capacity} />
        <Stat label="entradas" value={hash.entries} />
      </div>
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Bits bajos</th>
            <th>Prof. local</th>
            <th>Entradas</th>
            <th>Punteros = 2^(gd−ld)</th>
            <th>Páginas de desbordamiento</th>
          </tr>
        </thead>
        <tbody>
          {hash.buckets.map((bucket) => (
            <tr key={bucket.bits}>
              <td>{bucket.bits.slice(-bucket.local_depth).padStart(hash.global_depth, "·")}</td>
              <td className="is-number">{bucket.local_depth}</td>
              <td className="is-number">
                {bucket.entries} / {hash.bucket_capacity}
              </td>
              <td className="is-number">{bucket.pointers}</td>
              <td className="is-number">{bucket.overflow_pages}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {hash.bucket_count > hash.buckets.length && (
        <p className="modal__note">
          Se muestran {hash.buckets.length} de {hash.bucket_count} cubetas.
        </p>
      )}
    </>
  );
}

/**
 * Los MBR de cada nivel, dibujados a escala. La longitud se encoge por el coseno de la
 * latitud media para que las proporciones sean las del terreno y no las de la rejilla.
 */
function BoundsDrawing({ tree }: { tree: RTreeStructure }) {
  const extent = tree.levels[0].nodes[0]?.bounds ?? null;
  if (extent === null) {
    return null;
  }
  const [minLat, minLon, maxLat, maxLon] = extent;
  const shrink = Math.cos(((minLat + maxLat) / 2) * DEGREES_TO_RADIANS);
  const width = Math.max((maxLon - minLon) * shrink, Number.EPSILON);
  const height = Math.max(maxLat - minLat, Number.EPSILON);
  const scale = DRAWING_SIZE / Math.max(width, height);
  const box = (bounds: Bounds) => ({
    x: (bounds[1] - minLon) * shrink * scale,
    y: (maxLat - bounds[2]) * scale,
    width: (bounds[3] - bounds[1]) * shrink * scale,
    height: (bounds[2] - bounds[0]) * scale,
  });
  return (
    <svg
      aria-label="Rectángulos envolventes de los nodos del R-Tree"
      className="mbr"
      role="img"
      viewBox={`0 0 ${width * scale} ${height * scale}`}
    >
      {tree.levels.map((level, depth) =>
        level.nodes.map((node, index) =>
          node.bounds === null ? null : (
            <rect
              className={level.kind === "hojas" ? "mbr__box mbr__box--leaf" : "mbr__box"}
              key={`${depth}-${index}`}
              {...box(node.bounds)}
            />
          ),
        ),
      )}
    </svg>
  );
}

function RTreeView({ tree }: { tree: RTreeStructure }) {
  return (
    <>
      <div className="stats">
        <Stat label="altura (niveles)" value={tree.height} />
        <Stat label="puntos indexados" value={tree.entries} />
        <Stat label="nodos" value={tree.nodes} />
        <Stat label="puntos por hoja" value={tree.leaf_capacity} />
        <Stat label="hijos por nodo interno" value={tree.branch_capacity} />
        <Stat label="ocupación mínima" value={`${(tree.minimum_fill * PERCENT).toFixed(0)} %`} />
      </div>
      <table className="grid grid--compact">
        <thead>
          <tr>
            <th>Nivel</th>
            <th>Nodos</th>
            <th>Entradas</th>
            <th>Ocupación media</th>
          </tr>
        </thead>
        <tbody>
          {tree.levels.map((level, depth) => (
            <tr key={depth}>
              <td>
                {depth} · {depth === 0 ? "raíz" : level.kind}
              </td>
              <td className="is-number">{level.node_count.toLocaleString("es")}</td>
              <td className="is-number">{level.entry_count.toLocaleString("es")}</td>
              <td className="is-number">
                {averageFill(
                  level,
                  level.kind === "hojas" ? tree.leaf_capacity : tree.branch_capacity,
                ).toFixed(1)}{" "}
                %
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <BoundsDrawing tree={tree} />
      <p className="modal__note">
        Cada rectángulo es el MBR de un nodo: en azul los nodos internos y en verde las hojas.
        Una búsqueda solo abre los nodos cuyo rectángulo toca la zona consultada.
      </p>
    </>
  );
}

function IndexView({ structure }: { structure: IndexStructure }) {
  if (structure.kind === "bplustree") return <TreeView tree={structure} />;
  if (structure.kind === "rtree") return <RTreeView tree={structure} />;
  return <HashView hash={structure} />;
}

function FileView({ storage }: { storage: HeapStorage | SequentialStorage }) {
  if (storage.kind === "heap") {
    return (
      <div className="stats">
        <Stat label="filas" value={storage.records} />
        <Stat label="páginas" value={storage.pages} />
        <Stat label="ranuras por página" value={storage.slots_per_page} />
      </div>
    );
  }
  return (
    <div className="stats">
      <Stat label="filas" value={storage.records} />
      <Stat label="páginas principales" value={storage.main_pages} />
      <Stat label="ranuras por página" value={storage.slots_per_page} />
      <Stat label="en desbordamiento" value={storage.overflow_records} />
      <Stat label="lápidas" value={storage.deleted_records} />
      <Stat label="desperdicio" value={`${(storage.waste_ratio * PERCENT).toFixed(1)} %`} />
    </div>
  );
}

export default function StructureDialog({ table, onClose }: StructureDialogProps) {
  const [structure, setStructure] = useState<TableStructure | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchStructure(table.name)
      .then(setStructure)
      .catch((reason: unknown) =>
        setError(reason instanceof ApiError ? reason.failure.error : "No se pudo leer la estructura"),
      );
  }, [table.name]);

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div
        className="modal__panel modal__panel--wide"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="modal__header">
          <h2>Estructura física de «{table.name}»</h2>
          <button className="icon-button" onClick={onClose} type="button">
            ✕
          </button>
        </header>
        <div className="modal__body">
          {error !== null && <div className="notice notice--error">{error}</div>}
          {structure === null && error === null && <p className="modal__note">Leyendo páginas…</p>}
          {structure !== null && (
            <>
              <section className="structure">
                <h3 className="structure__title">
                  Archivo de datos — {structure.organization.replace("_", " ")}
                </h3>
                {structure.storage.kind === "bplustree" ? (
                  <TreeView tree={structure.storage} />
                ) : (
                  <FileView storage={structure.storage} />
                )}
              </section>
              {structure.indexes.map((index) => (
                <section className="structure" key={index.name}>
                  <h3 className="structure__title">
                    Índice {index.name} — {index.method} sobre {index.column}
                  </h3>
                  <IndexView structure={index.structure} />
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
