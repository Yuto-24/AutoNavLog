import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("NavMate display name leaves compatibility identities intact", () => {
  for (const [path, expected] of [
    ["../index.html", "<title>NavMate — NAV2 地上準備</title>"],
    ["../src/components/Header.tsx", '<div className="brand-name">NavMate</div>'],
    ["../src/components/InformationDialog.tsx", "NavMateのお知らせ"],
    ["../src/App.tsx", "NavMateを起動しています"],
    ["../src/App.tsx", '<div className="loading-mark">N</div>'],
    ["../src/App.tsx", "NavMateは非公式の地上準備支援ツールです。"],
    ["../static/404.html", "NavMateを再読み込み"],
    ["../src/localProjectRepository.ts", '"autonavlog.projects"'],
    ["../src/applicationSession.ts", '"autonavlog.working-session.v1"'],
    ["../src/browserStorage.ts", '"autonavlog.weather."'],
    ["../src/localWeather.ts", '"autonavlog.weather.msm.v1"'],
    ["../src/releaseNotes.ts", '"autonavlog.information.lastSeenUpdate"'],
    ["../src/releaseNotes.ts", '"autonavlog.information.lastSeenRelease"'],
    ["../src/releaseNotes.ts", '"autonavlog.information.knownIssuesSeen"'],
    ["../src/navLogExport.ts", '"autonavlog.navlog"'],
    ["../src/legacyApplication.ts", '"X-AutoNavLog-Session"'],
  ]) {
    expect(read(path), path).toContain(expected);
  }
});

test("adopted NavMate icon metadata uses same-origin assets without offline claims", () => {
  const manifest = JSON.parse(read("../public/manifest.webmanifest"));
  expect(manifest).toMatchObject({ id: "/", name: "NavMate", short_name: "NavMate", start_url: "/", scope: "/", display: "standalone" });
  expect(manifest.icons).toEqual([192, 512].map(size => ({ src: `/navmate-${size}.png`, sizes: `${size}x${size}`, type: "image/png", purpose: "any" })));
  for (const [path, size] of [["favicon-32.png", 32], ["apple-touch-icon.png", 180], ["navmate-192.png", 192], ["navmate-512.png", 512]] as const) {
    const png = readFileSync(new URL(`../public/${path}`, import.meta.url));
    expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(png.readUInt32BE(16), path).toBe(size);
    expect(png.readUInt32BE(20), path).toBe(size);
    expect(png[25], path).toBe(6); // RGBA, no flattening of adopted transparency.
  }
  const ico = readFileSync(new URL("../public/favicon.ico", import.meta.url));
  expect(ico.readUInt16LE(2)).toBe(1);
  expect(ico.readUInt16LE(4)).toBe(3);
  expect([0, 1, 2].map(index => ico[6 + index * 16])).toEqual([16, 32, 48]);
  const html = read("../index.html");
  for (const href of ["/favicon.ico", "/favicon-32.png", "/apple-touch-icon.png", "/manifest.webmanifest"]) expect(html).toContain(`href="${href}"`);
  expect(html).not.toContain("favicon.svg");
});
