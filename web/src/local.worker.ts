import { expose } from "comlink";
import { loadPyodide } from "pyodide";

async function initialize() {
  const pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/",
  });
  await pyodide.loadPackage(["pydantic", "micropip", "tzdata", "numpy"]);
  await pyodide.runPythonAsync(`
import micropip
await micropip.install(["defusedxml==0.7.1", "geographiclib==2.1"])
`);
  const assetUrl = (name: string) => new URL(`${import.meta.env.BASE_URL}local/${name}`, self.location.origin);
  const manifestResponse = await fetch(assetUrl("manifest.json"), { cache: "no-cache" });
  if (!manifestResponse.ok) throw new Error(`Local asset: manifest (${manifestResponse.status})`);
  const manifest = await manifestResponse.json() as { wheels: string[]; data: string; sha256: Record<string, string> };
  for (const filename of [...manifest.wheels, manifest.data]) {
    if (!/^[a-zA-Z0-9_.-]+$/.test(filename)) throw new Error("Invalid Local asset name");
    const response = await fetch(assetUrl(filename));
    if (!response.ok) throw new Error(`Local asset: ${filename} (${response.status})`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    // hashlib works on plain HTTP localhost as well as HTTPS (Safari acceptance uses HTTPS).
    pyodide.globals.set("asset_bytes", bytes);
    pyodide.globals.set("asset_hash", manifest.sha256[filename]);
    pyodide.runPython(`
import hashlib
if hashlib.sha256(bytes(asset_bytes.to_py())).hexdigest() != asset_hash:
    raise ValueError("Local asset SHA-256 mismatch")
del asset_bytes, asset_hash
`);
    if (filename.endsWith(".whl")) {
      pyodide.FS.writeFile(`/tmp/${filename}`, bytes);
      pyodide.globals.set("wheel_path", `/tmp/${filename}`);
      await pyodide.runPythonAsync(`await micropip.install("emfs:" + wheel_path, deps=False)`);
      pyodide.FS.unlink(`/tmp/${filename}`);
    } else {
      pyodide.unpackArchive(bytes, "zip", { extractDir: "/home/pyodide" });
    }
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
