import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const releaseHeader = /^## ((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)) - (\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2} JST)?)$/;
const sectionHeader = /^### (.+)$/;
const bullet = /^- (.+)$/;
// This is the content identifier produced from the v1.10.0 Information payload in a4a92da.
// It permits a legacy release-version marker to migrate only when that exact content was seen.
const legacyReleaseInformationIds = {
  "1.10.0": "information:sha256:1aaa6d69025442f35549a1ea36157c30112fd54ff0ec7611ee40c8409e827ca0",
};

function fail(line, message) {
  throw new Error(`CHANGELOG.md:${line}: ${message}`);
}

function validDate(value) {
  const date = value.slice(0, 10);
  const parsed = new Date(`${date}T00:00:00.000Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === date
    && (value.length === 10 || /^\d{4}-\d{2}-\d{2} (?:[01]\d|2[0-3]):[0-5]\d JST$/.test(value));
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

function withoutSourceLines(block) {
  if (block.kind === "paragraph") return { kind: block.kind, text: block.text };
  return { kind: block.kind, items: block.items.map((item) => ({ text: item.text })) };
}

export function informationPayload(releases) {
  return {
    releases: releases.map((release) => ({
      version: release.version,
      date: release.date,
      summary: release.summary.map(withoutSourceLines),
      sections: release.sections.map((section) => ({
        title: section.title,
        blocks: section.blocks.map(withoutSourceLines),
      })),
    })),
  };
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

const allowedSections = ["追加", "改善", "変更", "修正"];

export function parseReleaseNotes(source) {
  const releases = parseChangelog(source);
  const before = source.split(/^## /m)[0];
  if (before.split(/\r?\n/).some((line) => line.trim() && !line.startsWith("# "))) {
    throw new Error("RELEASE_NOTES.md: unexpected content before releases");
  }
  for (const release of releases) {
    if (release.summary.length) throw new Error("RELEASE_NOTES.md: version introduction is not allowed");
    let previous = -1;
    for (const section of release.sections) {
      const order = allowedSections.indexOf(section.title);
      if (order < 0 || order <= previous) throw new Error("RELEASE_NOTES.md: invalid or out-of-order section");
      previous = order;
      for (const block of section.blocks) {
        if (block.kind !== "list" || block.items.some((item) => item.text.includes("\n"))) {
          throw new Error("RELEASE_NOTES.md: only single-line bullets are allowed");
        }
        if (block.items.some((item) => /(?:Issue\s*#?\s*\d+|#\d+)/i.test(item.text))) {
          throw new Error("RELEASE_NOTES.md: Issue numbers are developer metadata");
        }
      }
    }
  }
  return releases;
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

export async function generateReleaseNotes({ changelogPath, releaseNotesPath, knownIssuesPath, packagePath, outputPath }) {
  const [changelog, userNotes, knownSource, packageJson] = await Promise.all([
    readFile(changelogPath, "utf8"), readFile(releaseNotesPath, "utf8"),
    readFile(knownIssuesPath, "utf8"), readFile(packagePath, "utf8"),
  ]);
  const developer = parseChangelog(changelog);
  const releases = parseReleaseNotes(userNotes);
  const versions = (items) => items.map(({ version, date }) => [version, date]);
  if (JSON.stringify(versions(developer)) !== JSON.stringify(versions(releases))) {
    throw new Error("CHANGELOG / RELEASE_NOTES version order and dates must match");
  }
  const packageVersion = JSON.parse(packageJson).version;
  if (releases[0].version !== packageVersion) throw new Error("Package version does not match latest release");
  const information = buildInformation(releases, parseKnownIssues(knownSource));
  // date is the exact visible label; dateTime represents timed releases with an explicit zone.
  information.releases = information.releases.map((release) => ({ ...release,
    dateTime: release.date.length === 10 ? release.date : `${release.date.slice(0, 10)}T${release.date.slice(11, 16)}:00+09:00`,
  }));
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify({ information,
    compatibility: { legacyReleaseInformationIds },
  }, null, 2)}\n`, "utf8");
}

const ownFile = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === ownFile) {
  const webRoot = resolve(dirname(ownFile), "..");
  const repositoryRoot = resolve(webRoot, "..");
  generateReleaseNotes({
    changelogPath: resolve(repositoryRoot, "CHANGELOG.md"),
    releaseNotesPath: resolve(repositoryRoot, "RELEASE_NOTES.md"),
    knownIssuesPath: resolve(repositoryRoot, "KNOWN_ISSUES.md"),
    packagePath: resolve(webRoot, "package.json"),
    outputPath: resolve(webRoot, "src/generated/releaseNotes.json"),
  }).catch((error) => { console.error(error.message); process.exitCode = 1; });
}
