import { fileURLToPath } from "node:url";

/** La carpeta de datasets diminutos que armó `e2e/run.sh` y que el compose monta. */
export const DATASETS_DIR = process.env.MINIGESTOR_DATASETS_DIR ?? fileURLToPath(new URL("../.state/datasets", import.meta.url));
