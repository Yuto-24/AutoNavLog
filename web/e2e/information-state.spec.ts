import { expect, test } from "@playwright/test";

import { hasUnreadInformation, hasUnreadKnownIssues, markInformationSeen } from "../src/releaseNotes";
import type { InformationData } from "../src/releaseNotes";

const baselineUpdateId =
  "information:sha256:1aaa6d69025442f35549a1ea36157c30112fd54ff0ec7611ee40c8409e827ca0";
const lastSeenReleaseKey = "autonavlog.information.lastSeenRelease";
const lastSeenUpdateKey = "autonavlog.information.lastSeenUpdate";

test("an unchanged a4a92da legacy marker migrates to the update marker without deleting it", () => {
  const values = new Map([[lastSeenReleaseKey, "1.10.0"]]);
  const fakeStorage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
  const originalWindow = globalThis.window;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: fakeStorage },
  });
  const data: InformationData = {
    information: { id: baselineUpdateId, releases: [] },
    compatibility: { legacyReleaseInformationIds: { "1.10.0": baselineUpdateId } },
  };

  try {
    expect(hasUnreadInformation(data)).toBe(false);
    expect(values.get(lastSeenUpdateKey)).toBe(baselineUpdateId);
    expect(values.get(lastSeenReleaseKey)).toBe("1.10.0");

    const noticeOnlyUpdate: InformationData = {
      information: {
        id: "information:sha256:" + "b".repeat(64),
        releases: [],
        notices: [{ title: "Maintenance", text: "New schedule" }],
      },
      compatibility: { legacyReleaseInformationIds: {} },
    };
    expect(hasUnreadInformation(noticeOnlyUpdate)).toBe(true);
  } finally {
    Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
  }
});


test("Known Issue additions/edits warn, removals/order/releases notify normally, and metadata stays silent", () => {
  const values = new Map<string, string>();
  const originalWindow = globalThis.window;
  Object.defineProperty(globalThis, "window", { configurable: true, value: { localStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  } } });
  const first = { id: "first", bodyHash: "a", title: "First", description: ["body"], sections: [] };
  const second = { ...first, id: "second", bodyHash: "b", title: "Second" };
  const data: InformationData = { information: { id: "initial", releases: [], knownIssuesId: "ab", knownIssues: [first, second] }, compatibility: { legacyReleaseInformationIds: {} } };
  try {
    expect(hasUnreadKnownIssues(data)).toBe(true);
    expect(hasUnreadInformation(data)).toBe(true);
    markInformationSeen(data);
    expect(hasUnreadKnownIssues(data)).toBe(false);
    expect(hasUnreadInformation(data)).toBe(false);
    const changed = (id: string, issues = [first, second]): InformationData => ({ ...data, information: { ...data.information, id, knownIssues: issues } });
    for (const next of [changed("removed", [second]), changed("reordered", [second, first]), changed("release")]) {
      expect(hasUnreadInformation(next)).toBe(true);
      expect(hasUnreadKnownIssues(next)).toBe(false);
    }
    expect(hasUnreadInformation(changed("initial", [{ ...first, id: "renamed" }, second]))).toBe(false);
    expect(hasUnreadKnownIssues(changed("updated", [{ ...first, bodyHash: "changed" }, second]))).toBe(true);
    expect(hasUnreadKnownIssues(changed("added", [first, second, { ...first, id: "third" }]))).toBe(true);
    const next = changed("updated", [{ ...first, bodyHash: "changed" }, second]);
    markInformationSeen(next);
    expect(hasUnreadKnownIssues(next)).toBe(false);
    expect(hasUnreadInformation(next)).toBe(false);
  } finally {
    Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
  }
});
