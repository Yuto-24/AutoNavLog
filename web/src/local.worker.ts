import { expose } from "comlink";
import { loadPyodide } from "pyodide";

async function initialize() {
  const pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/",
  });
  await pyodide.loadPackage(["pydantic", "micropip", "tzdata"]);
  await pyodide.runPythonAsync(`
import micropip
await micropip.install(["defusedxml==0.7.1", "geographiclib==2.1"])
`);
  for (const filename of ["autonavlog.whl", "data.zip"]) {
    const response = await fetch(new URL(`${import.meta.env.BASE_URL}local/${filename}`, self.location.origin));
    if (!response.ok) throw new Error(`Local asset: ${filename} (${response.status})`);
    pyodide.unpackArchive(await response.arrayBuffer(), "zip", { extractDir: "/home/pyodide" });
  }
  pyodide.runPython(`
from pathlib import Path
from autonavlog.local import LocalApplication
local_application = LocalApplication(Path("/home/pyodide/data"))
`);
  return pyodide;
}

let ready: ReturnType<typeof initialize> | undefined;
// Serialize mutations while the runtime initializes; Comlink supplies the RPC protocol.
let queue = Promise.resolve();
const workerApi = {
  request(path: string, body?: unknown): Promise<string> {
    const result = queue.then(async () => {
      const pyodide = await (ready ??= initialize());
      pyodide.globals.set("local_path", path);
      pyodide.globals.set("local_body_json", JSON.stringify(body ?? {}));
      return pyodide.runPython(`
import json
local_application.dispatch(local_path, json.loads(local_body_json))
`) as string;
    });
    queue = result.then(() => undefined, () => undefined);
    return result;
  },
};
export type LocalWorker = typeof workerApi;
expose(workerApi);
