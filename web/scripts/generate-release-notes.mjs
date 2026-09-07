import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const releaseHeader = /^## ((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)) - (\d{4}-\d{2}-\d{2})$/;
const sectionHeader = /^### (.+)$/;
const bullet = /^- (.+)$/;

function fail(line, message) {
  throw new Error(`CHANGELOG.md:${line}: ${message}`);
}

function validDate(value) {
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function compareVersions(left, right) {
  const leftParts = left.split(".").map(Number);
  const rightParts = right.split(".").map(Number);
  for (let index = 0; index < leftParts.length; index += 1) {
    if (leftParts[index] !== rightParts[index]) return leftParts[index] - rightParts[index];
  }
  return 0;
}

function appendText(block, line, lineNumber) {
  if (block.kind === "list") {
    const item = block.items.at(-1);
    if (!item) fail(lineNumber, "list continuation has no list item");
    item.text += `\n${line.trim()}`;
  } else {
    block.text += `${block.text ? "\n" : ""}${line.trim()}`;
  }
}

/** Parse the deliberately small CHANGELOG dialect used by this repository. */
export function parseChangelog(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const releases = [];
  let release = null;
  let section = null;
  let block = null;

  const finishBlock = () => { block = null; };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const lineNumber = index + 1;
    const header = line.match(releaseHeader);
    if (header) {
      finishBlock();
      if (!validDate(header[2])) fail(lineNumber, `invalid release date '${header[2]}'`);
      if (releases.some((item) => item.version === header[1])) {
        fail(lineNumber, `duplicate release version '${header[1]}'`);
      }
      release = { version: header[1], date: header[2], summary: [], sections: [] };
      releases.push(release);
      section = null;
      continue;
    }
    if (!release) {
      if (/^\s*#{2,}/.test(line)) fail(lineNumber, "release header must be '## X.Y.Z - YYYY-MM-DD'");
      if (bullet.test(line)) {
        fail(lineNumber, "content must follow a release header");
      }
      continue;
    }
    if (line.startsWith("## ")) fail(lineNumber, "release header must be '## X.Y.Z - YYYY-MM-DD'");
    const nextSection = line.match(sectionHeader);
    if (nextSection) {
      finishBlock();
      if (!nextSection[1].trim()) fail(lineNumber, "section title is required");
      section = { title: nextSection[1], blocks: [] };
      Object.defineProperty(section, "_line", { value: lineNumber });
      release.sections.push(section);
      continue;
    }
    if (line === "") { finishBlock(); continue; }
    const destination = section ? section.blocks : release.summary;
    const item = line.match(bullet);
    if (item) {
      block = { kind: "list", items: [{ text: item[1], line: lineNumber }] };
      destination.push(block);
      continue;
    }
    if (/^\s*(?:#{1,6}\s|`{3,}|~{3,}|={3,}|-{3,}|\*{3,}|_{3,})/.test(line)
      || /^\s*(?:[-*+] |\d+\. |>|\|)/.test(line)) {
      fail(lineNumber, "unsupported Markdown block");
    }
    if (/^\s+/.test(line)) {
      if (!block) fail(lineNumber, "indented content must continue a paragraph or list item");
      appendText(block, line, lineNumber);
      continue;
    }
    if (block?.kind === "list") {
      fail(lineNumber, "list items must start with '- ' or use an indented continuation");
    } else if (block?.kind === "paragraph") {
      appendText(block, line, lineNumber);
    } else {
      block = { kind: "paragraph", text: line, line: lineNumber };
      destination.push(block);
    }
  }
  if (releases.length === 0) fail(1, "no release entries found");
  for (let releaseIndex = 0; releaseIndex < releases.length; releaseIndex += 1) {
    const item = releases[releaseIndex];
    const following = releases[releaseIndex + 1];
    if (following && compareVersions(item.version, following.version) <= 0) {
      fail(lines.findIndex((line) => line.includes(following.version)) + 1, "release versions must be in descending order");
    }
    if (item.summary.length === 0 && item.sections.length === 0) {
      fail(lines.findIndex((line) => line.includes(item.version)) + 1, "release has no content");
    }
    for (const itemSection of item.sections) {
      if (itemSection.blocks.length === 0) fail(itemSection._line, "section has no content");
    }
  }
  return releases;
}

export async function generateReleaseNotes({ changelogPath, packagePath, outputPath }) {
  const [changelog, packageJson] = await Promise.all([
    readFile(changelogPath, "utf8"), readFile(packagePath, "utf8"),
  ]);
  const releases = parseChangelog(changelog);
  let packageVersion;
  try { packageVersion = JSON.parse(packageJson).version; } catch { throw new Error("web/package.json: invalid JSON"); }
  if (releases[0].version !== packageVersion) {
    throw new Error(`web/package.json: version '${packageVersion}' does not match CHANGELOG latest '${releases[0].version}'`);
  }
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify({ releases }, null, 2)}\n`, "utf8");
}

const ownFile = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === ownFile) {
  const webRoot = resolve(dirname(ownFile), "..");
  const repositoryRoot = resolve(webRoot, "..");
  generateReleaseNotes({
    changelogPath: resolve(repositoryRoot, "CHANGELOG.md"),
    packagePath: resolve(webRoot, "package.json"),
    outputPath: resolve(webRoot, "src/generated/releaseNotes.json"),
  }).catch((error) => { console.error(error.message); process.exitCode = 1; });
}
