import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { generateReleaseNotes, informationId, informationPayload, parseChangelog } from "./generate-release-notes.mjs";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

test("preserves release order, paragraphs, sections, lists and inline code", () => {
  const releases = parseChangelog(`# Change log\n\n## 1.2.0 - 2026-09-07\n\nIntro with \`code\`.\ncontinued line.\n\n### Added\n\n- First item with \`token\`\n  continued detail\n- Second item\n\n## 1.1.0 - 2026-09-06\n\n### Fixed\n\n- Earlier item\n`);
  assert.equal(releases[0].version, "1.2.0");
  assert.deepEqual(releases[0].summary, [{ kind: "paragraph", text: "Intro with `code`.\ncontinued line.", line: 5 }]);
  assert.equal(releases[0].sections[0].blocks[0].items[0].text, "First item with `token`\ncontinued detail");
  assert.equal(releases[1].sections[0].title, "Fixed");
});

test("rejects invalid dates, duplicate versions and unsupported headings with a line", () => {
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-02-30\n\n- x\n"), /CHANGELOG\.md:1/);
  assert.throws(() => parseChangelog("## 01.0.0 - 2026-01-01\n\n- x\n"), /release header/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n- x\n\n## 1.0.0 - 2026-01-02\n"), /CHANGELOG\.md:5/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n#### no\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n### Empty\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n- x\n\n## 1.1.0 - 2026-01-02\n\n- y\n"), /descending order/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n~~~\ncode\n~~~\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\nTitle\n---\n"), /CHANGELOG\.md:4/);
  assert.throws(() => parseChangelog("## 1.0.0 - 2026-01-01\n\n  ### nested heading\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("# only a title\n\n- no release\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("Intro\n\n##1.2.0 - 2026-01-01\n\nIntro\n"), /CHANGELOG\.md:3/);
  assert.throws(() => parseChangelog("Intro\n\n  ## 1.2.0 - 2026-01-01\n\nIntro\n"), /CHANGELOG\.md:3/);
});

test("parses the current changelog's adjacent 1.1.0 and 1.0.0 releases without dropping its intro", async () => {
  const changelog = await readFile(join(repositoryRoot, "CHANGELOG.md"), "utf8");
  const releases = parseChangelog(changelog);
  const oneOneIndex = releases.findIndex((release) => release.version === "1.1.0");
  assert.equal(releases[oneOneIndex + 1]?.version, "1.0.0");
  const oneOne = releases[oneOneIndex];
  const oneZero = releases[oneOneIndex + 1];
  assert.match(oneOne?.sections[0]?.blocks[0]?.items[0]?.text ?? "", /`RJFM → OMARU`[\s\S]*`UMK\/RCA 5,500 ft`/);
  assert.deepEqual(oneZero?.summary.map((block) => block.kind === "paragraph" ? block.text : block), [
    "AutoNavLogの初回正式版です。従来のNAV2計算・安全ゲートに加え、FTD訓練と\nWeb上のCheck Point計画、端末幅に応じた地図操作を正式版の対象に含めます。",
  ]);
  assert.equal(oneZero?.sections[0]?.title, "追加");
  assert.match(oneZero?.sections[0]?.blocks[0]?.items[0]?.text ?? "", /0〜5,000 ft/);
});

test("generates deterministic JSON and rejects a package version mismatch", async () => {
  const directory = await mkdtemp(join(tmpdir(), "autonavlog-release-notes-"));
  const changelogPath = join(directory, "CHANGELOG.md");
  const packagePath = join(directory, "package.json");
  const outputPath = join(directory, "nested", "releaseNotes.json");
  await writeFile(changelogPath, "## 2.0.0 - 2026-09-07\n\nIntro.\n\n### Added\n\n- Item\n", "utf8");
  await writeFile(packagePath, '{"version":"2.0.0"}\n', "utf8");
  await generateReleaseNotes({ changelogPath, packagePath, outputPath });
  const first = await readFile(outputPath, "utf8");
  const generated = JSON.parse(first);
  assert.match(generated.information.id, /^information:sha256:[0-9a-f]{64}$/);
  assert.equal(generated.information.releases[0].summary[0].line, undefined);
  await generateReleaseNotes({ changelogPath, packagePath, outputPath });
  assert.equal(await readFile(outputPath, "utf8"), first);
  await writeFile(packagePath, '{"version":"2.0.1"}\n', "utf8");
  await assert.rejects(
    generateReleaseNotes({ changelogPath, packagePath, outputPath }),
    /does not match CHANGELOG latest/,
  );
});

test("information ID includes all display content but excludes source line numbers", () => {
  const first = parseChangelog("## 2.0.0 - 2026-09-07\n\nIntro\n\n### Added\n\n- Item\n");
  const shifted = parseChangelog("\n\n## 2.0.0 - 2026-09-07\n\nIntro\n\n### Added\n\n- Item\n");
  assert.equal(informationId(informationPayload(first)), informationId(informationPayload(shifted)));
  const changed = parseChangelog("## 2.0.0 - 2026-09-07\n\nChanged\n\n### Added\n\n- Item\n");
  assert.notEqual(informationId(informationPayload(first)), informationId(informationPayload(changed)));

  const noticeOnly = { ...informationPayload(first), notices: [{ title: "Maintenance", text: "A" }] };
  const changedNotice = { ...informationPayload(first), notices: [{ title: "Maintenance", text: "B" }] };
  assert.notEqual(informationId(noticeOnly), informationId(changedNotice));
  assert.equal(
    informationId({ notices: noticeOnly.notices, releases: noticeOnly.releases }),
    informationId(noticeOnly),
  );
});
