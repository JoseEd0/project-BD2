import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const run = promisify(execFile);
const SCRIPT = join(import.meta.dirname, "..", "scripts", "generate-types.ts");
const CONTRACT = {
  openapi: "3.1.0",
  info: { title: "tiny", version: "0.1.0" },
  paths: {
    "/health": {
      get: {
        responses: {
          "200": {
            description: "ok",
            content: { "application/json": { schema: { type: "object", properties: { status: { type: "string" } } } } },
          },
        },
      },
    },
  },
};

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "web-kit-types-"));
  await mkdir(join(workspace, "openapi"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

function generate(...folders: string[]) {
  return run(process.execPath, [SCRIPT, ...folders.map((folder) => join(workspace, folder))]);
}

describe("generate-types", () => {
  it("writes one types file per contract and removes the ones whose contract is gone", async () => {
    await writeFile(join(workspace, "openapi", "shop.json"), JSON.stringify(CONTRACT));
    await mkdir(join(workspace, "generated"));
    await writeFile(join(workspace, "generated", "retired.ts"), "export {};\n");
    await generate("openapi", "generated");
    expect(await readdir(join(workspace, "generated"))).toEqual(["shop.ts"]);
    const types = await readFile(join(workspace, "generated", "shop.ts"), "utf8");
    expect(types).toContain('"/health"');
    expect(types).toContain("status?: string");
  });

  it("writes the same bytes every time, so the CI can diff them", async () => {
    await writeFile(join(workspace, "openapi", "shop.json"), JSON.stringify(CONTRACT));
    await generate("openapi", "first");
    await generate("openapi", "second");
    const [first, second] = await Promise.all(
      ["first", "second"].map((folder) => readFile(join(workspace, folder, "shop.ts"), "utf8")),
    );
    expect(first).toBe(second);
  });

  it("refuses a folder without contracts instead of emptying the generated types", async () => {
    await expect(generate("openapi", "generated")).rejects.toMatchObject({ stderr: expect.stringContaining("no hay contratos") });
  });

  it("explains its usage when a folder is missing", async () => {
    await expect(generate("openapi")).rejects.toMatchObject({ code: 2, stderr: expect.stringContaining("uso:") });
  });
});
