# AutoNavLog project rules

## Responsive workflow order

- At 1,240 px and narrower, keep the primary workflow in one vertical direction: input, route and map, readiness, NAV LOG, then contextual guidance associated with the NAV LOG.
- Do not reorder a later workflow step above an earlier one at intermediate widths; the user must not need to scroll back up to continue after checking the route or map.
- Keep wide layouts above 1,240 px aligned as a three-column input, route, and readiness workspace unless a feature explicitly requires another layout.
- Every layout change must include browser regression coverage at an intermediate width around 1,100 px and a wide width above 1,240 px. Assert the relative vertical or horizontal positions of the workflow regions, not only their visibility.

## Plan execution

- GPT-5.6 Sol orchestrates and, in principle, does not implement directly.
- Delegate implementation, investigation, and testing to Terra, Luna, or GPT-5.4 through Sub Agents or Sub Threads as needed.
- Require review by a model independent from the implementing model; implementer self-review alone is not completion.

## Reuse prior knowledge

- Before new work, inspect relevant repository rules, docs, Issues, PRs, prior decisions, known failures, and analogous implementations.
- Reuse the furthest reliable result to avoid unnecessary re-investigation, reimplementation, and repetition of known failures, but validate differences against the current code, specification, and dependencies before applying it.

## Durable knowledge

- Preserve only decision-changing findings: supported design rationale, root causes, failed approaches, effective fixes, constraints, recurring patterns, and useful validation results.
- Record those findings in an appropriate durable location.
- Exclude raw logs, routine history, code-obvious facts, and duplicates.

## Generalize carefully

- Keep a one-time result as an Observation or Learning; promote it to a Pattern only after confirmation elsewhere.
- Promote a Pattern to a Rule only when it is reproducible and well supported; verify and update conflicting rules when evidence changes.

## Instruction context

- Keep always-loaded instructions small. Before adding to them, decide whether the information is needed for every task; otherwise externalize detailed decisions, historical failures, evidence, and similar cases, loading them only when relevant.
