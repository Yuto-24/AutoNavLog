# Captured HTML bytes for Issue #266

These are unchanged, unexecuted canonical GET payloads, **not published originals**.
Neither fixture is learned by the verifier at runtime.

- `observed-root.html`: `/tmp/autonavlog-issue266-readonly-20261017/root.body`,
  2026-10-17 09:00:32 JST, 1022 bytes,
  SHA-256 `44e6e333e078407e1ee5f582d6fb0905fb2fda0fddfc41e0343001449135353f`.
  The reviewed insertion occupies byte offsets `[639,1006)` (366 script bytes + LF).
- `changed-root.html`: `/tmp/autonavlog-issue266-validation/live/serving-root.body`,
  2026-10-17 09:58:17 JST, 1022 bytes,
  SHA-256 `563d09a93aee94ebf8d68b0cc69eaf205f2b97e32c10c7ef3c884fdacf763340`.
  A later src/integrity pair is explicitly **not allowed**. This fixture proves rejection.

Both were fetched with the owner-authorized `AutoNavLog-StaticOperations/1.0`
identity and `Cache-Control: no-cache`, without redirects, retries or query strings.
Current published original HTML is unavailable. Removing the identified script + LF
from either capture matches the recorded inventory (655 bytes,
`5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1`);
that does not establish direct comparison to an independently retained original.
See [evidence and owner-confirmed settings](../../../docs/evidence/issue-265-266/2026-10-17-root-analytics.md).
