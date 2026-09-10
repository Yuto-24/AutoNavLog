import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const versionPattern = /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/;
const releaseHeader = /^## ((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$/;
const allowedSections = ["追加", "改善", "変更", "修正"];
const legacyReleaseInformationIds = {
  "1.10.0": "information:sha256:1aaa6d69025442f35549a1ea36157c30112fd54ff0ec7611ee40c8409e827ca0",
};

function fail(line, message) {
  throw new Error(`CHANGELOG.md:${line}: ${message}`);
}

function compareVersions(left, right) {
  const a = left.split(".").map(BigInt);
  const b = right.split(".").map(BigInt);
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
  }
  return 0;
}

/** Validate the complete CHANGELOG and expose only user content to Information. */
export function parseChangelog(source) {
  const releases = [];
  let current = null;
  let audience = null;
  let section = null;
  for (const [index, raw] of source.replace(/\r\n/g, "\n").split("\n").entries()) {
    const line = raw.trimEnd();
    const number = index + 1;
    const header = line.match(releaseHeader);
    if (header) {
      if (releases.length && compareVersions(header[1], releases.at(-1).version) >= 0) {
        fail(number, "release versions must descend");
      }
      current = { version: header[1], sections: [], developer: [] };
      releases.push(current);
      audience = section = null;
      continue;
    }
    if (line.startsWith("## ")) fail(number, "release header must be ## X.Y.Z");
    if (!line.trim()) continue;
    if (!current) {
      if (line.startsWith("# ") || !line.startsWith("#")) continue;
      fail(number, "invalid heading");
    }
    if (line === "### 利用者向け") {
      if (audience) fail(number, "duplicate audience");
      audience = "user";
    } else if (line === "### 開発者向け") {
      if (audience !== "user") fail(number, "開発者向け must follow 利用者向け");
      audience = "developer";
      section = null;
    } else if (line.startsWith("#### ")) {
      const title = line.slice(5);
      if (audience !== "user" || !allowedSections.includes(title)
        || current.sections.some((item) => item.title === title)) {
        fail(number, "invalid user section");
      }
      if (section && allowedSections.indexOf(title) <= allowedSections.indexOf(section.title)) {
        fail(number, "user sections must follow the defined order");
      }
      section = { title, items: [] };
      current.sections.push(section);
    } else if (/^- \S/.test(line)) {
      if (audience === "user" && section) {
        if (/\bIssue\s*#?\s*\d+|#\d+/i.test(line)) {
          fail(number, "Issue references belong in 開発者向け");
        }
        section.items.push(line.slice(2));
      } else if (audience === "developer") {
        current.developer.push(line.slice(2));
      } else fail(number, "bullet has no section");
    } else if (audience === "developer" && line.startsWith("  ")) {
      if (!current.developer.length) fail(number, "continuation has no bullet");
      current.developer[current.developer.length - 1] += ` ${line.trim()}`;
    } else fail(number, "unsupported changelog content");
  }
  if (!releases.length) fail(1, "no release entries found");
  for (const release of releases) {
    if (!release.sections.length || release.sections.some((item) => !item.items.length)
      || !release.developer.length) {
      throw new Error(`CHANGELOG.md: release ${release.version} requires nonempty 利用者向け and 開発者向け`);
    }
  }
  return releases.map(({ version, sections }) => ({
    version,
    summary: [],
    sections: sections.map(({ title, items }) => ({
      title,
      blocks: [{ kind: "list", items: items.map((text) => ({ text })) }],
    })),
  }));
}

export function informationPayload(releases) {
  return { releases };
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function informationId(payload) {
  return `information:sha256:${createHash("sha256").update(stableJson(payload)).digest("hex")}`;
}

export function parseKnownIssues(source) {
  const issues = [];
  let pending = {};
  let current = null;
  let section = null;
  for (const [index, line] of source.replace(/\r\n/g, "\n").split("\n").entries()) {
    const bad = (message) => { throw new Error(`KNOWN_ISSUES.md:${index + 1}: ${message}`); };
    if (!line.trim()) continue;
    if (/^<!-- [^:]* -->$/.test(line)) continue;
    const metadata = line.match(/^<!-- ([a-z-]+): (.+) -->$/);
    if (metadata) {
      const [, key, value] = metadata;
      if (!["id", "github-issue"].includes(key) || Object.hasOwn(pending, key)) bad("unknown or duplicate metadata");
      if (key === "id") {
        if (Object.keys(pending).length || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value)) bad("invalid id/metadata order");
        current = null;
        section = null;
      } else if (!pending.id || !/^[1-9]\d*$/.test(value)) bad("github-issue must follow id and be positive");
      pending[key] = value;
    } else if (line === "# 既知の不具合" && !issues.length && !Object.keys(pending).length) {
      continue;
    } else if (line.startsWith("## ")) {
      if (!pending.id || !line.slice(3).trim()) bad("id and title required");
      if (issues.some((issue) => issue.id === pending.id)) bad("duplicate issue id");
      current = { id: pending.id, title: line.slice(3), description: [], sections: [] };
      issues.push(current);
      pending = {};
      section = null;
    } else if (current && line.startsWith("### ")) {
      const title = line.slice(4);
      if (!["影響する条件", "回避方法"].includes(title) || current.sections.some((item) => item.title === title)) bad("unknown or duplicate section");
      section = { title, items: [] };
      current.sections.push(section);
    } else if (current && section && /^- \S.*$/.test(line)) {
      section.items.push(line.slice(2));
    } else if (current && !section && !/^\s*(?:[#<>`~*+|]|- |\d+\. )/.test(line)) {
      current.description.push(line);
    } else bad("invalid Known Issue structure");
  }
  if (Object.keys(pending).length) throw new Error("KNOWN_ISSUES.md: metadata without issue");
  for (const issue of issues) {
    if (!issue.description.length || issue.sections.some((item) => !item.items.length)) {
      throw new Error("KNOWN_ISSUES.md: description and nonempty optional sections required");
    }
  }
  return issues;
}

export function knownIssueBody({ title, description, sections }) {
  return { title, description, sections };
}

export function buildInformation(releases, knownIssues) {
  const visibleIssues = knownIssues.map(knownIssueBody);
  const visible = { ...informationPayload(releases), knownIssues: visibleIssues };
  const issues = knownIssues.map((issue) => ({ ...issue, bodyHash: informationId(knownIssueBody(issue)) }));
  return { ...visible, id: informationId(visible), knownIssues: issues, knownIssuesId: informationId(visibleIssues) };
}

export async function generateReleaseNotes({ changelogPath, knownIssuesPath, versionPath, outputPath }) {
  const [changelog, knownSource, versionSource] = await Promise.all([
    readFile(changelogPath, "utf8"),
    readFile(knownIssuesPath, "utf8"),
    readFile(versionPath, "utf8"),
  ]);
  const version = versionSource.replace(/\r?\n$/, "");
  if (!versionPattern.test(version)) throw new Error("VERSION must be X.Y.Z");
  const releases = parseChangelog(changelog);
  if (releases[0].version !== version) throw new Error("VERSION does not match latest release");
  const information = { ...buildInformation(releases, parseKnownIssues(knownSource)), version };
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify({
    information,
    compatibility: { legacyReleaseInformationIds },
  }, null, 2)}\n`, "utf8");
}

const ownFile = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === ownFile) {
  const webRoot = resolve(dirname(ownFile), "..");
  const repositoryRoot = resolve(webRoot, "..");
  generateReleaseNotes({
    changelogPath: resolve(repositoryRoot, "CHANGELOG.md"),
    knownIssuesPath: resolve(repositoryRoot, "KNOWN_ISSUES.md"),
    versionPath: resolve(repositoryRoot, "VERSION"),
    outputPath: resolve(webRoot, "src/generated/releaseNotes.json"),
  }).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
