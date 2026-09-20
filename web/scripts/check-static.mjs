import { resolve } from "node:path";
import { checkArtifact } from "./static-artifact.mjs";
const args = process.argv.slice(2);
console.log(JSON.stringify(checkArtifact(resolve(args.find(arg => !arg.startsWith("--")) ?? "dist-static"),
  { freshWeather: !args.includes("--allow-expired-weather") }), null, 2));
