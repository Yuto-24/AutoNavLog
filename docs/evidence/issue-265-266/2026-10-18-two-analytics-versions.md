# Two fixed Analytics versions and actual serving completion (#266 / #269)

## Authorization and provenance

PR #269 extends #268 HEAD `0a7add84c2616c80846ff9aea40723b48040ec3e`, which depends
on #267 HEAD `1f04cd3adc406df6d21ba8cf1eeb56d03af7f969`. All three were still open
Drafts when this follow-up started. The previous #269 HEAD was
`f8f82e5a8bebef66c78031c0466188f8da90adff`; existing checkouts were preserved.

On 2026-10-18, after reviewing the direct script GET and SHA-512 result, the owner
explicitly authorized adding the new saved script alongside the old one. Only
these two complete src/integrity/attributes/beacon/position/newline combinations
are admitted. No runtime response is used to learn or extend the allowlist.
The [initial record](2026-10-17-root-analytics.md) retains the original rejection,
the owner-confirmed Cloudflare settings, and the unavailable-original limitation.

### Direct JS GET, 2026-10-18 01:44:03 JST

Requested and final URL (identical):
`https://static.cloudflareinsights.com/beacon.min.js/vce701823e78c4301a35b7e07294d87301792088814092`

- One GET, HTTP **200**, **no redirect**, **31,853 actual bytes**, complete body.
- MIME `text/javascript;charset=UTF-8`; Content-Encoding absent; identity encoding requested.
- Honest `AutoNavLog-StaticOperations/1.0` identity, no-cache, default TLS certificate/hostname
  verification; no query, authentication, retry, redirect following or JavaScript execution.
- Computed SHA-512 (hex):
  `50a364f066f190007129c5d8b8b90ed7a12578b19491cbc95ada523661311344e8a3765a140c53b1c35611cd4e740bf9fc18a41dac1aa5209a883d970a6a7e07`
- Computed SRI **exactly equals** saved HTML integrity:
  `sha512-UKNk8GbxkABxKcXYuLkO16EleLGUkcvJWtpSNmExE0Too3ZaFAxTscNWEc1OdAv5/BikHawapSCaiD2XCmp+Bw==`.
  Python hashlib and OpenSSL independently produced the same digest of the saved bytes.

Cloudflare's [official FAQ](https://developers.cloudflare.com/web-analytics/faq/)
shows `https://static.cloudflareinsights.com/beacon.min.js` as its snippet and CSP
script source, and documents supplying integrity with automatic injection.
This establishes the official host/path. The specific suffix and expected digest
come from saved HTML, not an independently published vendor checksum list.
Raw JS, headers, JSON result and report are retained under
`/tmp/autonavlog-issue266-script-get-20261018/`.

## Fixed pairs and unchanged checks

`ROOT_ANALYTICS_INSERTIONS` contains exactly two full byte literals. Each is a
366-byte script followed by one LF. These are hashes of the **HTML insertion**,
not of the downloaded JS:

| Version | src suffix | Insertion SHA-256 |
| --- | --- | --- |
| Original | `v4bc70e2c01a94c73b74392e4234840661791215815920` | `b6be984c8109d0590449b61544a081955ec1c3e10f453a97cb17afa54924969f` |
| Newly approved | `vce701823e78c4301a35b7e07294d87301792088814092` | `139e1299438bceecb0a33814f2e7947ec22febf072f1d0f533087a1ee91bd461` |

The existing full integrity values are kept with their matching src. All other
attributes and exact JSON remain pinned: module, crossorigin anonymous,
version 2024.11.0, public token 95f71c67cb5a41d3ab040fe18f7bcaa4, r=1, spa=2.
Exactly one approved insertion may occur, following the original two spaces and
immediately preceding `</body>\n</html>\n`. The selected insertion hash is recorded.
Two copies, both versions together, crossed src/integrity pairs, any unapproved
version, altered attributes/settings/position/LF, or other HTML changes fail.
After removing only the selected literal, candidate inventory bytes/SHA-256 must
still match. Original HTML with no insertion remains accepted unchanged.

No changes to root status/MIME/cache/budget/redirect/request-identity checks, root
ETag recording, manifest ETag/bytes/hash, missing-asset 404, Rules, MSM freshness,
Pages target or inventory gates. Raw-response retention is unchanged. Future
Cloudflare changes still require evidence and explicit allowlist review.

## Actual production verification, 2026-10-18 01:50:21 JST

`Operator.verify_serving()` was called once with freshly fetched canonical
release.json as its inventory input. Only the HTTP transport was wrapped to save
evidence; all actual validators ran unchanged. No build, prepare or publish ran.
All five requests (release, root, manifest, missing asset, separate MSM catalog)
used the established honest operations identity with no redirects, query or retries.
The request/body records and script are in
`/tmp/autonavlog-issue266-two-scripts-20261018/`.

| Gate | Actual result |
| --- | --- |
| Release GET/input | 200 / application/json / no-cache / ETag present; 28,481 bytes; SHA-256 `54147dd5af57ce47a903e478d449d7c91ad1e15668ad1bc759e726effc07e946` |
| Release identity | clean v1.20.1 / `a4de2a226dfa205b8be42fb62a710a1b06271ed8` |
| Root HTTP/MIME/cache/budget | **PASS**, 200 / text/html / no-cache / 1,022 bytes; ETag absent, recorded |
| Root content | **PASS**, approved new insertion; raw SHA-256 `563d09a93aee94ebf8d68b0cc69eaf205f2b97e32c10c7ef3c884fdacf763340`; compared 655 bytes / `5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1` |
| Manifest | **PASS**, actual method reached it; 200 / application/json / no-cache / ETag present; 510 bytes / `471db61d9e3d1f6b4e61a0620c94c07b931712dd3126e23e0c54a964c6f40c58` matches inventory |
| Missing commit-specific asset | **PASS**, actual method reached it and received 404 |
| Actual `verify_serving()` | **OK**, completed root, manifest and missing-asset checks |
| Separate `operations.monitor()` | **BLOCKED**, catalog expired 2026-10-15 22:35:46.791957 JST |
| Full `Operator.verify()`, independent candidate inventory gate, Rules, Pages target | **NOT RUN** |

Root CF-Ray: `a4c0cd41fbf8fca2-KIX`; manifest: `a4c0cd42d842d1c1-KIX`;
missing asset: `a4c0cd43ad3019d5-KIX`. The missing path was
`/local/a4de2a226dfa205b8be42fb62a710a1b06271ed8.missing.whl`.
Catalog GET succeeded (555,017 bytes, SHA-256
`5aa1cc614e01b871767024c6a11644576339f26b4fc6bd12100b94dcfbfc7384`), but freshness
failed. It is not made successful by the root fix.

**The current publication-time original remains unavailable.** This proves real
serving verification against the freshly observed inventory, not direct comparison
with an independently retained original, full production acceptance, or a new
candidate's publication. No reconstructed body is presented as an original.

## Validation and merge decision boundaries

Regressions exercise exact HTML with/without ETag and both fixed insertions through
the real serving sequence. Both versions retain mutation/status/MIME/cache/redirect
and downstream failure coverage, including crossed pairs and mixed double insertion.
Exact test counts, CI and independent/CodeRabbit review results are recorded on #269.

Review the stack in dependency order **#267 -> #268 -> #269**. Merge remains
unperformed and requires explicit approval. After each parent merge, retarget and
revalidate the child against main; do not regard today's stacked checks as proof of
the future integration SHA. Once all necessary changes are merged and reflected,
pin the final tooling SHA before updating prepare/publication instructions.

MSM expiry stays a separate blocking item. Fresh generation, prepare/publish,
Rules/settings/billing/credential changes, schedules and approved-SHA changes
were not performed or authorized by this implementation follow-up.
