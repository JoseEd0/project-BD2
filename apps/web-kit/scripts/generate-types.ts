/**
 * Genera `<carpeta de tipos>/<servicio>.ts` desde cada `<carpeta de contratos>/<servicio>.json`.
 * Cada interfaz lo corre sobre su propio contrato:
 *
 *     node ../../web-kit/scripts/generate-types.ts openapi src/api/generated
 *
 * Usa el `openapi-typescript` del kit (`npm --prefix apps/web-kit ci`), así que todas las
 * interfaces generan sus tipos con la misma versión.
 *
 * Borra los tipos de un contrato que ya no está, para que la carpeta generada sea siempre
 * la imagen exacta de los contratos versionados.
 */
import { readdir, rm, writeFile, mkdir } from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const CONTRACT_EXTENSION = ".json";
const TYPES_EXTENSION = ".ts";

async function serviceNames(directory: string, extension: string): Promise<string[]> {
  const entries = await readdir(directory).catch(() => []);
  return entries.filter((entry) => extname(entry) === extension).map((entry) => basename(entry, extension)).sort();
}

async function generate(contractsDirectory: string, typesDirectory: string): Promise<void> {
  const contracts = await serviceNames(contractsDirectory, CONTRACT_EXTENSION);
  if (contracts.length === 0) {
    throw new Error(`no hay contratos ${CONTRACT_EXTENSION} en ${contractsDirectory}`);
  }
  await mkdir(typesDirectory, { recursive: true });
  for (const service of contracts) {
    const source = pathToFileURL(resolve(contractsDirectory, service + CONTRACT_EXTENSION));
    const target = join(typesDirectory, service + TYPES_EXTENSION);
    await writeFile(target, COMMENT_HEADER + astToString(await openapiTS(source)));
    console.log(target);
  }
  const stale = (await serviceNames(typesDirectory, TYPES_EXTENSION)).filter((name) => !contracts.includes(name));
  await Promise.all(stale.map((name) => rm(join(typesDirectory, name + TYPES_EXTENSION))));
}

const [contractsDirectory, typesDirectory] = process.argv.slice(2);
if (contractsDirectory === undefined || typesDirectory === undefined) {
  console.error("uso: node scripts/generate-types.ts <carpeta de contratos> <carpeta de tipos>");
  process.exit(2);
}
await generate(contractsDirectory, typesDirectory);
