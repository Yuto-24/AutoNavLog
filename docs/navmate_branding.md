# NavMate branding integration (#199)

This minimal candidate is based on main `58127b3` (1.22.9) and the existing
branding commit `dfef4ba`, for the **v2.0.0 naming/icon release boundary**.
PR #282 and PR #283 features are explicitly excluded; they remain separate,
unmerged work. PR #200 remains historical reference, not the source of this patch.
This candidate does not establish that production publication is accepted.

The user-approved name `NavMate` replaces the human-facing product name in the
Web header, browser title, startup/information/disclaimer text, static 404 page,
server fallback, API metadata, CLI help and current product documentation.
Existing wording otherwise remains unchanged. The user approved the source PNG
on 2026-10-23. Its SHA-256 is
`30faff87f7820ae4e62cc4c3b1bca016137b3af041672166472aaf5cf166c3e6`.
The exact original is retained at `assets/branding/navmate-adopted-source.png`.
User-authorized deterministic crop `(26, 28, 1228, 1230)` removes extraneous
outer edges, preserving the rounded tile and aircraft. No alpha cleanup,
flattening, recoloring or image generation is performed.
`scripts/build_navmate_icons.py` reproduces PNG (32/180/192/512 px) and ICO
(16/32/48 px) derivatives using Pillow 12.3.0 and checks the original checksum first.
The manifest declares `purpose: any`, not an unverified maskable safe area;
no service worker or offline capability is added.

## Compatibility boundaries

- Keep Python distribution/package `autonavlog`, CLI `autonavlog-web`, web package
  `autonavlog-web`, and the application interfaces unchanged
- Keep `AUTONAVLOG_*`, Compose projects/services/images, `autonavlog-data`, paths,
  authentication headers, owner-storage hash domain and generated-worktree marker
- Keep IndexedDB, Local/Session Storage, Weather Cache and export identifiers,
  Project schemas, Last Calculation and all existing migration logic
- Keep persisted reference-data provenance labels and historical release/evidence
  records; no branding-specific data migration is introduced
- Keep existing GitHub repository URLs and operational identity checks until the
  separately coordinated repository rename is performed and verified

## Remaining release gates

- Complete #187 icon packaging and real-device acceptance using the adopted #240 source.
  Test iPhone/iPad Safari and Android Chrome home-screen name/icon, login return
  and existing storage; do not imply offline support or introduce a service worker.
- Independently review the final integrated diff, run release validation and
  required tests, and verify changed display text in the supported runtime
- VERSION is 2.0.0; CHANGELOG contains only naming/icon changes above the
  unchanged history from 1.22.9. #282/#283 features are not release gates for this
  minimal branding release.
- Re-sweep current Open Issues and coordinate the `Yuto-24/NavMate` repository
  rename near release; validate CI, publication gates and links after that rename

Repository rename, production deployment, merge and Firestore Rules deployment
are not performed by this candidate. Legacy import remains enabled.

## Separate production publication gate

The general release path accepts an explicit candidate commit. The existing
`--rules-transition-v1227` exception only authorizes its original pinned source
pair and must not be relabeled or bypassed for v2.0.0. Before publication, review
a candidate-specific transition with evidence for both the old application/new
Rules and candidate application/new Rules. Verify the actually deployed Rules,
indexes and schema. Keep current fail-closed checks intact; this candidate does
not authorize a Rules change or establish live production compatibility.
