# NavMate agent guide

## Coordination and review

- GPT-6 Astra and GPT-6.1 Sol are both used in this project. The model selected for the task performs investigation, implementation, and testing directly. Preserve the user's model and reasoning-effort selection; do not delegate other work unless the user explicitly requests it.
- Review repository changes in a separate, read-only context with the other model: GPT-6.1 Sol reviews Astra's changes, and GPT-6 Astra reviews Sol's changes. Explicitly select the reviewer model; a separate agent or `/review` alone does not establish model independence. If the other model is unavailable, report the review as blocked. Self-review alone is not sufficient. See `docs/agent_workflow.md` for the review handoff and configuration boundaries.
- Before substantive work, inspect the repository documentation, Issues/PRs, prior decisions, and analogous code relevant to the task. Keep the inspection proportional to the change. Reuse prior knowledge only after checking it against the current code, specification, and dependencies.
- Keep reusable, non-obvious findings in the appropriate external documentation. Do not turn AGENTS.md into a work log or duplicate detailed evidence.
- When adding or changing user-facing UI copy, use Gemini CLI to determine the wording. Codex, GPT, and other implementation agents must not choose the final wording themselves. User-provided wording takes precedence. If Gemini CLI is unavailable, preserve existing copy where possible or leave the final wording unresolved and report the blocker instead of inventing it.

## Product constraints

- At 1,240 px and below, the primary flow is input, route/map, readiness, NAV LOG, then NAV LOG guidance, in that order. Do not move a later step above an earlier one or require the user to scroll back up after checking the route/map. Above 1,240 px, retain the input / route / readiness three-column workspace unless the feature requires otherwise.
- Any layout change needs browser coverage around 1,100 px and above 1,240 px, asserting workflow-region order/position rather than visibility alone. See `docs/web-design/README.md` for the UI reference.
- For release-management, version metadata, documentation, or CI changes without user-facing behavior or layout changes, skip browser automation, screenshots, and visual checks unless the user explicitly requests them. Use focused source, build, and health checks instead.
- Docker Compose is the supported local/Legacy runtime; the production delivery path is Static Web as described in `README.md` and `docs/static_production.md`. Local Compose health does not establish Static Web production acceptance. Preserve the `autonavlog-data` volume: routine updates and stops must not use `docker compose down -v`. Follow `README.md` and `docs/cloudflare_tunnel.md` for environment-specific startup and exposure rules.
- Ordinary feature/fix PRs use `Release: not-required` with a `Reason:` and keep root `VERSION` and `CHANGELOG.md` unchanged. Record pending release notes in the PR body under `## Pending release notes`, or explicitly state `None` when there are none. Only an explicit release-preparation PR uses `Release: required`, increments `VERSION`, and adds the finalized `CHANGELOG.md` section; do not rewrite published release notes. This applies to both `main` and `release/1.x`. `VERSION` is the sole NavMate version source; do not place it in web package manifests or README. Run `python scripts/validate_release.py`, `pytest tests/unit/test_version.py`, and `npm --prefix web run typecheck`. Do not change the separately managed `src/autonavlog/version.py` or `jma-msm-wind` version.

## Codex worktrees

- In Codex worktrees, run `scripts/codex/setup_worktree.sh` before Docker Compose work. The generated environment isolates the Compose project, image, host port, and named volume; do not target or reuse the primary `autonavlog` runtime from a worktree.
- Do not assume port 8123 in a Codex worktree. Read `AUTONAVLOG_HOST_PORT` from the generated `.env` or use the URL printed by the setup script.
- The only exception to the volume-preservation rule is `scripts/codex/cleanup_worktree.sh`, and only for generated projects whose `COMPOSE_PROJECT_NAME` matches `autonavlog-wt-*`. Never run `docker compose down -v` manually against the primary NavMate runtime.

## Change and delivery safety

- Preserve unrelated user changes; stage and modify only the requested paths. Do not use destructive commands such as `git reset --hard`, `git checkout --`, broad recursive deletion, or volume deletion unless the user explicitly authorizes the exact target.
- Unless the user explicitly limits scope, completing requested repository edits includes required checks and independent review; updating the local Docker Compose runtime while preserving the data volume and verifying health and changed behavior; committing scoped changes, pushing the branch, and creating or updating the task PR. Do not seek separate confirmation for these default completion actions; report the local runtime update, commit, PR URL, and hosted CI accurately, and state any blocked stage explicitly. Merges, remote production deploys, and external publishing outside a PR still require explicit authorization.
- If GitHub Actions cannot run or complete because of GitHub-hosted billing, spending, or usage limits, treat the current workflow definitions as the source of truth and reproduce the applicable CI checks locally as closely as practical. Post to the PR which hosted checks were blocked, the GitHub-side blocking reason, the local commands or checks run, and their results. Do not report hosted CI as passed; state that it was blocked and locally reproduced. If any hosted-only check cannot be reproduced locally, identify it explicitly and leave that gate unresolved.
- Before an explicitly authorized merge, independently confirm CI, reviews (including CodeRabbit), unresolved threads, `reviewDecision`, `mergeable`, and `mergeStateStatus`; all must be in an acceptable final state. Report any unresolved or stale gate rather than merging.

## CodeRabbit review requests

- Obtain an initial CodeRabbit review of the PR. Check the automatic review status first; request it manually only when it has not started or a documented retry is needed. Do not submit a duplicate request while a review is queued or running.
- Do not request another CodeRabbit review merely because HEAD changed. Re-review is warranted when an unresolved finding needs reviewer confirmation, or the subsequent diff changes behavior, security, data handling, or an important specification. Batch related fixes and the affected checks before one targeted request.
- For comments, docstrings, formatting, or other non-functional edits, inspect the actual diff rather than relying on the file type. If independent diff review and applicable checks establish that behavior and previously reviewed contracts are unchanged, record that evidence instead of requesting CodeRabbit again. Changes to agent instructions, release procedures, security guidance, or executable examples can affect behavior and must not be treated as harmless merely because they are documentation.
- Before any additional request, record its reason, the reviewed commit, current HEAD, remaining findings, and whether automatic review already covers the diff. If a completed automatic review already covers the target diff and no outstanding finding requires another reviewer response, do not request it again; if such a review is queued or running, wait. Do not claim that an older review covers a newer commit without checking and reporting the intervening changes.
- Respect rate limits and the stated retry time. A quota reset alone is not a reason to re-request; reassess whether review is still necessary. Do not post repeated requests while waiting or change paid plans to obtain more reviews.
- Preserve all existing independent-review requirements and actual repository gates. Required latest-commit approval, required checks, unresolved conversations, and effective change requests take precedence over the optimization above. Do not dismiss reviews, weaken protection, or mark unreviewed work as approved to bypass a gate; report any required gate that cannot be verified or satisfied. Merge and deployment still require their existing authorization.

When a repository's documentation and its existing code disagree, follow the conventions used in the existing code, and in your final reply point out each inconsistency you found between the documentation and the code

## Reusable knowledge

Before work, consult [the shared knowledge index](docs/knowledge/INDEX.md) and only the entries relevant to the task; follow [the knowledge workflow](docs/knowledge/README.md) to verify applicability and preserve non-obvious findings with evidence through incremental updates. Keep detailed lessons out of this file, reuse existing canonical documents, and do not create an entry when nothing reusable was learned.

## Reader-facing writing

Before creating or updating PR bodies, Issue bodies, comments, ROADMAPs, work reports, or final replies for this project, read and follow [the writing standard](docs/writing-style.md). This applies to GitHub and chat messages as well as repository files. Run its submission checklist before sending or publishing. Keep the standard canonical rather than duplicating its detailed rules here. Writing rules do not authorize changes, publication, or additional notifications.
