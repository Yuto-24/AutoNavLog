import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  buildInformation, generateReleaseNotes, informationId, informationPayload,
  parseChangelog, parseKnownIssues,
} from "./generate-release-notes.mjs";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const release = (version = "2.0.0", user = "新機能", developer = "Issue #1で実装") => (
  `## ${version}\n\n### 利用者向け\n\n#### 追加\n\n- ${user}\n\n### 開発者向け\n\n- ${developer}\n`
);

test("Information exposes only user sections in release order, without dates or developer content", () => {
  const releases = parseChangelog(`# 変更履歴\n\n${release()}\n${release("1.0.0", "以前の機能")}`);
  assert.deepEqual(releases.map((item) => item.version), ["2.0.0", "1.0.0"]);
  assert.deepEqual(releases[0].sections, [{
    title: "追加", blocks: [{ kind: "list", items: [{ text: "新機能" }] }],
  }]);
  assert.doesNotMatch(JSON.stringify(releases), /Issue|developer|date/);
});

test("rejects malformed releases, empty or misplaced sections, and developer references in user content", () => {
  for (const source of [
    release("01.0.0"), release("1.0.0 - 2026-09-10"), release() + release(),
    release("1.0.0") + release(), release().replace("#### 追加", "#### 配布"),
    release().replace("- 新機能", ""), release().replace("- Issue #1で実装", ""),
    release().replace("#### 追加", "#### 修正\n- 修正\n#### 追加"),
    release().replace("#### 追加", "#### 追加\n#### 改善"),
    release().replace("- 新機能", "- Issue #2で修正"),
    release().replace("- 新機能", "# injected heading\n- 新機能"),
    release().replace("### 開発者向け", "### その他"),
    release().replace("### 利用者向け", "### 開発者向け"),
    "## Unreleased\n", "# Empty changelog\n", `### rogue\n${release()}`,
  ]) assert.throws(() => parseChangelog(source), /CHANGELOG.md/);
});

test("preserves adjacent historical releases and their user content", async () => {
  const releases = parseChangelog(await readFile(join(repositoryRoot, "CHANGELOG.md"), "utf8"));
  const index = releases.findIndex((item) => item.version === "1.1.0");
  assert.equal(releases[index + 1]?.version, "1.0.0");
  assert.match(JSON.stringify(releases[index]), /宮崎/);
  assert.match(JSON.stringify(releases[index + 1]), /訓練用に風を指定/);
  assert.equal(releases.at(-1).version, "0.3.0");
});

test("generation is deterministic and rejects a VERSION mismatch or invalid version", async () => {
  const directory = await mkdtemp(join(tmpdir(), "autonavlog-release-notes-"));
  const options = {
    changelogPath: join(directory, "CHANGELOG.md"),
    knownIssuesPath: join(directory, "KNOWN_ISSUES.md"),
    versionPath: join(directory, "VERSION"),
    outputPath: join(directory, "nested", "releaseNotes.json"),
  };
  await writeFile(options.changelogPath, release());
  await writeFile(options.knownIssuesPath, "# 既知の不具合\n");
  await writeFile(options.versionPath, "2.0.0\n");
  await generateReleaseNotes(options);
  const first = await readFile(options.outputPath, "utf8");
  const generated = JSON.parse(first);
  assert.equal(generated.information.version, "2.0.0");
  assert.match(generated.information.id, /^information:sha256:[0-9a-f]{64}$/);
  assert.equal(generated.information.releases[0].sections[0].blocks[0].items[0].line, undefined);
  assert.match(generated.compatibility.legacyReleaseInformationIds["1.10.0"], /^information:sha256:/);
  await generateReleaseNotes(options);
  assert.equal(await readFile(options.outputPath, "utf8"), first);
  for (const value of ["2.0.1", "v2.0.0", "2.0.0\n2.0.1", " 2.0.0", "2.0.0\n\n"]) {
    await writeFile(options.versionPath, value);
    await assert.rejects(generateReleaseNotes(options), /VERSION/);
  }
});

test("information ID tracks visible content but ignores developer edits and source formatting", () => {
  const first = informationPayload(parseChangelog(release()));
  const shifted = informationPayload(parseChangelog(`\n\n${release()}`));
  const developerEdit = informationPayload(parseChangelog(release("2.0.0", "新機能", "Issue #3で内部を変更")));
  assert.equal(informationId(first), informationId(shifted));
  assert.equal(informationId(first), informationId(developerEdit));
  assert.notEqual(informationId(first), informationId(informationPayload(parseChangelog(release("2.0.0", "変更")))));
  const notice = { ...first, notices: [{ title: "Maintenance", text: "A" }] };
  assert.notEqual(informationId(notice), informationId({ ...first, notices: [{ title: "Maintenance", text: "B" }] }));
  assert.equal(informationId(notice), informationId({ notices: notice.notices, releases: first.releases }));
});

test("Known Issues validate fixed structure and preserve order, hiding all management metadata from hashes", () => {
  const issue = "<!-- id: saved-plan -->\n<!-- github-issue: 123 -->\n## 計画を開けません\n以前の計画で発生します。\n### 回避方法\n- 開き直してください。\n";
  const parsed = parseKnownIssues(issue);
  assert.deepEqual(parseKnownIssues(issue + "<!-- 以下は任意です。不要な項目は削除してください。 -->\n"), parsed);
  const baseline = buildInformation([], parsed);
  assert.equal(baseline.id, buildInformation([], parseKnownIssues(issue.replace("123", "456").replace("saved-plan", "renamed"))).id);
  assert.notEqual(baseline.id, buildInformation([], parseKnownIssues(issue.replace("以前", "一部"))).id);
  assert.deepEqual(parseKnownIssues("# 既知の不具合\n"), []);
  for (const bad of [issue + issue, issue.replace("github-issue", "severity"),
    issue.replace("### 回避方法", "### その他"), "<!-- id: missing -->", "## Title\nBody",
    "<!-- id: missing -->\n## Title", issue.replace("- 開き直してください。", "not a bullet")]) {
    assert.throws(() => parseKnownIssues(bad));
  }
  const second = parseKnownIssues(issue.replace("saved-plan", "second").replace("計画を開けません", "別のお知らせ"))[0];
  const forward = buildInformation([], [...parsed, second]);
  const reversed = buildInformation([], [second, ...parsed]);
  assert.notEqual(forward.id, reversed.id);
  assert.notEqual(forward.knownIssuesId, reversed.knownIssuesId);
  assert.equal(forward.knownIssues[0].bodyHash, reversed.knownIssues[1].bodyHash);
});
