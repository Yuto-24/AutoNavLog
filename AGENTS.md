# AutoNavLog project rules

## Responsive workflow order

- At 1,240 px and narrower, keep the primary workflow in one vertical direction: input, route and map, readiness, NAV LOG, then contextual guidance associated with the NAV LOG.
- Do not reorder a later workflow step above an earlier one at intermediate widths; the user must not need to scroll back up to continue after checking the route or map.
- Keep wide layouts above 1,240 px aligned as a three-column input, route, and readiness workspace unless a feature explicitly requires another layout.
- Every layout change must include browser regression coverage at an intermediate width around 1,100 px and a wide width above 1,240 px. Assert the relative vertical or horizontal positions of the workflow regions, not only their visibility.
