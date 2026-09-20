import { resolve } from "node:path";
import { checkArtifact } from "./static-artifact.mjs";
const args = process.argv.slice(2);
if (args.some(arg => arg.startsWith("--") && !["--allow-dirty", "--allow-expired-weather"].includes(arg))) {
  throw new Error("Unknown check:static option");
}
console.log(JSON.stringify(checkArtifact(resolve(args.find(arg => !arg.startsWith("--")) ?? "dist-static"),
  { freshWeather: !args.includes("--allow-expired-weather"), allowDirty: args.includes("--allow-dirty") }), null, 2));
