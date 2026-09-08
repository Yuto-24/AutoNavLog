# AutoNavLog agent guide

## Coordination and review

- GPT-6 Astra should normally perform the work directly and use Sub Agents as little as possible. Sub Agents may be used only for simple tasks; do not check their progress.
- Review code changes with a model independent of the implementer. Self-review alone is not sufficient.
- Before substantive work, inspect the relevant repository documentation, Issues/PRs, prior decisions, and analogous code. Reuse prior knowledge only after checking it against the current code, specification, and dependencies.
- Keep reusable, non-obvious findings in the appropriate external documentation. Do not turn AGENTS.md into a work log or duplicate detailed evidence.

## Product constraints

- At 1,240 px and below, the primary flow is input, route/map, readiness, NAV LOG, then NAV LOG guidance, in that order. Do not move a later step above an earlier one or require the user to scroll back up after checking the route/map. Above 1,240 px, retain the input / route / readiness three-column workspace unless the feature requires otherwise.
- Any layout change needs browser coverage around 1,100 px and above 1,240 px, asserting workflow-region order/position rather than visibility alone. See `docs/web-design/README.md` for the UI reference.
- Issue changes require a change fragment in `changes/` (see `changes/README.md`); documentation-only, test-only, comment/copy-only changes may be exempt when no release history is warranted. `KNOWN_ISSUES.md`-only updates require neither a fragment nor a version bump. Record an associated Issue in fragment metadata.
- Update release versions only with `scripts/prepare_release.py X.Y.Z` using Docker's `release-tools` stage. It consumes all fragments and synchronizes package versions and both release histories. Run `python scripts/validate_release.py`, `pytest tests/unit/test_version.py`, and `npm --prefix web run typecheck`. Do not change the separately managed `src/autonavlog/version.py` or `jma-msm-wind` version. Review ambiguous fragment requirements that CI cannot reliably identify.
- Docker Compose is the supported runtime. Preserve the `autonavlog-data` volume: routine updates and stops must not use `docker compose down -v`. Follow `README.md` and `docs/cloudflare_tunnel.md` for environment-specific startup and exposure rules.

## Change and delivery safety

- Preserve unrelated user changes; stage and modify only the requested paths. Do not use destructive commands such as `git reset --hard`, `git checkout --`, broad recursive deletion, or volume deletion unless the user explicitly authorizes the exact target.
- Unless the user explicitly limits scope, completing requested repository edits includes required checks and independent review; updating the local Docker Compose runtime while preserving the data volume and verifying health and changed behavior; committing scoped changes, pushing the branch, and creating or updating the task PR. Do not seek separate confirmation for these default completion actions; report the local runtime update, commit, PR URL, and hosted CI accurately, and state any blocked stage explicitly. Merges, remote production deploys, and external publishing outside a PR still require explicit authorization.
- Before an explicitly authorized merge, independently confirm CI, reviews (including CodeRabbit), unresolved threads, `reviewDecision`, `mergeable`, and `mergeStateStatus`; all must be in an acceptable final state. Report any unresolved or stale gate rather than merging.
