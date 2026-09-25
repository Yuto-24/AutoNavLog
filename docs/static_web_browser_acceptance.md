# Static Web browser acceptance (#122)

The target is the ordinary HTTPS Static Web page, not an installed PWA. Record the
origin, release/source SHA, browser and OS versions, device model, date, result and
any failure for each run. Use a fresh test Project name in the browser's normal
profile. Do not clear site data between Save and restart. A private window is a
separate storage lifetime and is not a valid restart check.

## Acceptance matrix

The evidence classes are **engine** (Chromium/WebKit application behavior),
**OS/viewport** (window and browser UI geometry), and **physical** (native input,
gesture, lifecycle and storage). An emulated device covers only the first class
and viewport geometry; it does not establish physical Safari/Chrome behavior.

| Issue #122 criterion or cross-cutting check | Class | Existing evidence / automated check | Remaining acceptance |
| --- | --- | --- | --- |
| Input → route/map → readiness → NAV LOG → Save → reload → Project open | Engine | `web/e2e/local-calculation.spec.ts` FTD Golden at 1100/1440 px; `web/e2e/static-production.spec.ts` production artifact; `web/e2e/browser-acceptance.spec.ts` Chromium/WebKit desktop, phone and tablet emulation | One short end-to-end run on each formally supported OS/browser pair; reuse a run for checks below |
| Browser process restart; IndexedDB Project/Last Calculation | Engine + physical storage | `web/e2e/persistence.spec.ts` launches a new Chromium process with the same profile; #121 [same-origin Pages rollback record](evidence/issue-121/remote-production-2026-09-24.md) verifies a restarted profile and saved data hashes | Restart each supported physical browser once, reopening the same normal profile and origin |
| Session draft reload / background → foreground / orientation | Engine + physical | `web/e2e/session.spec.ts` covers raw draft reload; browser acceptance test covers new tab and return; WebKit/Chromium emulation exercises the application state | Mobile devices: rotate and switch away/back before Save, then confirm draft and NAV LOG are unchanged |
| Local Storage / IndexedDB / OPFS | Engine | Session state uses `sessionStorage`; Information preference uses `localStorage`; Project and Last Calculation use IndexedDB; Weather uses disposable Cache Storage. `docs/local_persistence.md`, `web/e2e/persistence.spec.ts`, and the browser acceptance test cover these contracts. | Project restart is the durable-storage proof. OPFS is not an application data store in the current implementation; no OPFS persistence claim is made. |
| 1,240 px workflow order; desktop three columns; NAV LOG horizontal scroll | OS/viewport + engine | `web/e2e/web.spec.ts` and `web/e2e/platform.spec.ts` assert positions at mobile, 1100 and 1440 px; browser acceptance test scrolls the result table | Check that every action remains reachable at native viewport and with browser zoom/text scaling |
| Safe area / software keyboard / focus / touch target / long press | Physical | CSS/layout and modal focus are covered by `web/e2e/web.spec.ts`, `information.spec.ts`, `platform.spec.ts`; emulation cannot raise the native keyboard or prove notch/selection behavior | iPhone, iPad and Android native checks below |
| MAP pinch/pan versus page zoom/overscroll | Physical | `.map-frame` and `.route-map` gesture CSS asserted in `web/e2e/web.spec.ts` (#98) | One native touch gesture check per mobile browser/OS; include phone and tablet form factors |
| File picker return and modal/dialog focus | Engine + physical | `web/e2e/platform.spec.ts` covers picker cancellation and retained import; `web/e2e/clipboard-import.spec.ts` covers focus return | Native file chooser cancellation and focus restoration on one iOS and one Android device |
| #165 initial NAV LOG scroll | Engine + physical | Merged #165; `web/e2e/navlog-scroll.spec.ts` covers first and later results in Chromium, iPhone WebKit and iPad WebKit emulation | Observe initial result position during the iPhone/iPad end-to-end run |
| #166 Clipboard Paste UI and recovery | Engine + physical | Merged #166; `web/e2e/platform.spec.ts` covers activation, denial, cancellation and file/manual recovery through #120 Platform Capability | iPhone/iPad: native Paste UI and cancellation recovery once per device; it cannot be asserted by emulation |
| Real MSM on physical iPad Safari | Physical, reused | [#144 iPad acceptance](evidence/issue-144/ipad-safari-acceptance.md): cold/warm real MSM, NAV LOG, failure retention and no crash on 2026-09-16. Device/OS/browser versions were not recorded. | Reuse for calculation feasibility; the #122 run checks current Static origin and lifecycle/gesture behavior, without repeating real MSM. |
| Production Static hosting, no required API/Service Worker | Engine, reused | [#121 production audit](evidence/issue-121/remote-production-2026-09-24.md) and `web/e2e/static-production.spec.ts` | No repeat provider/deploy, weather, account or rollback audit for #122 |

## Minimal physical checklist

Run the core sequence once for **Windows Chromium, macOS Apple Silicon Safari,
macOS Apple Silicon Chromium, Linux Chromium, iPhone Safari, iPad Safari,
Android Chrome phone and Android Chrome tablet**. The Android phone/tablet may
share an OS/browser release, but both viewport classes need evidence. Record
device/OS/browser versions. Use the same known KML and FTD inputs as the browser
test; this avoids a fresh MSM dependency. Download
[`issue_43_golden.kml`](https://github.com/Yuto-24/AutoNavLog/raw/refs/heads/main/tests/fixtures/issue_43_golden.kml)
to each device. Use DATE `2026-09-11`, FTD, ground wind `360/15`, 5,000 ft wind
`270/30`, and RJFM / 米ノ津 / 玉名 departure altitudes `6500/7500/6500` ft.
The core sequence is: import KML,
confirm the map/route, enter three leg altitudes, create NAV LOG, Save, reload,
quit and reopen the browser, then open the saved Project. Expected: correct
workflow order, visible NAV LOG and unchanged saved Project/Last Calculation.

| Device | Operation | Expected result |
| --- | --- | --- |
| Desktop OS/browser pairs above | Core sequence; resize across 1240 px; horizontally scroll NAV LOG; open/close Information | Three columns when wide; one-way workflow when narrow; table scrolls independently; focus returns; saved Project opens after browser restart |
| iPhone Safari | Core sequence; focus an input with keyboard open; pan/pinch map, then scroll page; rotate and background/foreground; cancel file picker; try Paste and cancel it | Input/actions remain visible above keyboard and safe area; map gesture does not zoom the page; draft/result survive lifecycle changes; picker preserves state; native Paste UI appears and manual/file recovery works; first result scrolls into view |
| iPad Safari | Same iOS interactions, including landscape and portrait; check long press on map and NAV LOG | Same state/gesture/focus results, without unwanted text selection or page zoom; first result scrolls into view |
| Android Chrome phone/tablet | Core sequence; keyboard, map gestures, rotation/background, picker cancellation and long press | Same reachability, state retention and gesture isolation for each form factor |

Mark each row **pass**, **fail** (with exact step), or **not run**. A failure is
investigated as a browser regression under #122. Keep the Issue open while a
formal browser or physical-only row lacks evidence. Do not infer physical
acceptance from WebKit emulation or from #144's older calculation proof.

## Automated execution

Build and serve `web/dist-local` at `AUTONAVLOG_LOCAL_URL`. The test compares
the served HTML and Local manifest byte-for-byte with that build; record the
source SHA (`git rev-parse HEAD`) and any working-tree changes beside the run.
From `web/`, run:

```sh
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4174 npx playwright test --config playwright.browser-acceptance.config.ts
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4174 npx playwright test --config playwright.navlog-scroll.config.ts
```

The browser acceptance suite exercises the same application path in desktop
Chromium/WebKit, iPhone/iPad WebKit emulation and Android phone/tablet Chromium emulation.
The existing persistence, platform, session and static production suites remain
the source for their deeper contracts. A green emulated run is engine evidence,
not a physical-device pass. The `local-browser` CI job runs this suite against
its built Local static files before the separate TAF fixture build.

## Local run (2026-09-26)

Source was `8635fbd2f3772218bb6bc14ea56efd3e6a7997fe` with the #122 test/docs
changes in this branch. A prepared `build:local` output was served as ordinary
static files at `http://127.0.0.1:4174`; the browser suite checked the served
HTML and manifest against those build files. Results: desktop Chromium, desktop
WebKit, iPhone WebKit, iPad WebKit, Android phone Chromium and Android tablet
Chromium **6/6 passed** (five together, then the added tablet project). The
existing initial/recalculation NAV LOG scroll suite passed **3/3** (Chromium,
iPhone WebKit, iPad WebKit). The focused browser-process restart, KMZ picker
cancel, and Clipboard Paste cancellation recovery tests passed **3/3**.
`npm --prefix web run typecheck`, `python scripts/validate_release.py`, and
`pytest -q tests/unit/test_version.py` passed with the worktree Python 3.12
environment. The first localhost test attempt used the wrong port and did not
reach the application; it was rerun against the intended artifact. These runs
do not establish any unobserved physical OS/browser result.
