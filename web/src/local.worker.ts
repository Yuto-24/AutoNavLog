import { fetchDestinationTaf } from "./destinationTaf";
import { expose } from "comlink";
import { loadPyodide } from "pyodide";
import { LocalWeatherTransport, WeatherError } from "./localWeather";
import type { PreparedAsset } from "./localWeather";

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
const weather = new LocalWeatherTransport();

function weatherResult<T>(json: string): T {
  const value = JSON.parse(json);
  if (value.error) throw new WeatherError(value.error.code, value.error.message);
  return value as T;
}

async function acquireWeather(pyodide: Awaited<ReturnType<typeof initialize>>) {
  if (!pyodide.runPython("local_application.requires_weather()")) return;
  const asset = await weather.catalog(catalog => {
    pyodide.globals.set("weather_catalog", catalog);
    try {
      return weatherResult<PreparedAsset>(pyodide.runPython(
        "local_application.weather_plan(weather_catalog)",
      ) as string);
    } finally { pyodide.globals.delete("weather_catalog"); }
  });
  await weather.prepared(asset, bytes => {
    pyodide.globals.set("weather_bytes", bytes);
    pyodide.globals.set("weather_sha256", asset.sha256);
    try {
      weatherResult(pyodide.runPython(
        "local_application.accept_weather(bytes(weather_bytes.to_py()), weather_sha256)",
      ) as string);
    } finally {
      pyodide.globals.delete("weather_bytes");
      pyodide.globals.delete("weather_sha256");
    }
  });
}
// Serialize mutations while the runtime initializes; Comlink supplies the RPC protocol.
let queue = Promise.resolve();
const workerApi = {
  request(path: string, body?: unknown): Promise<string> {
    const result = queue.then(async () => {
      const pyodide = await (ready ??= initialize());
      pyodide.globals.set("local_path", path);
      pyodide.globals.set("local_dispatch_path", path);
      pyodide.globals.set("local_body_json", JSON.stringify(body ?? {}));
      const airport = pyodide.runPython(`
import json
local_application.destination_taf_airport(local_path, json.loads(local_body_json))
`) as string | undefined;
      if (airport) {
        const taf = await fetchDestinationTaf(import.meta.env.VITE_TAF_PROXY_URL, airport);
        pyodide.globals.set("local_taf_json", JSON.stringify(taf));
        pyodide.runPython(`local_application.set_destination_taf(json.loads(local_taf_json))`);
      }
      if (path === "calculate" || path === "updateAndRecalculate") {
        // Commit the same validated draft before acquisition as the facade does before
        // calculation. The existing Application adapter persists it on failure (#124).
        if (path === "updateAndRecalculate") {
          const updated = pyodide.runPython(`
import json
local_application.dispatch_response("updateProject", json.loads(local_body_json))
`) as string;
          if (JSON.parse(updated).error) return updated;
          pyodide.globals.set("local_dispatch_path", "calculate");
        }
        try { await acquireWeather(pyodide); }
        catch (error) {
          return JSON.stringify({ error: {
            code: error instanceof WeatherError ? error.code : "WEATHER_PROCESSING_FAILED",
            message: error instanceof WeatherError ? error.message : "MSMの端末内処理に失敗しました。",
          } });
        }
      }
      return pyodide.runPython(`
import json
local_application.dispatch_response(local_dispatch_path, json.loads(local_body_json), error_operation=local_path)
`) as string;
    });
    queue = result.then(() => undefined, () => undefined);
    return result;
  },
};
export type LocalWorker = typeof workerApi;
expose(workerApi);
