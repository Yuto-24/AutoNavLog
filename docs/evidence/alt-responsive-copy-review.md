# ALT table disclaimer removal

The user explicitly requested removal of this exact sentence, with no replacement:

> 本アプリは地表高を判定しないため、候補外の値は違法判定ではなく要確認表示です。

On 2026-11-02, the existing `agy` CLI executed a one-turn wording confirmation
with `--model gemini-4-argon-high --mode plan --output-format json`. The request
prohibited tool execution and file edits and specified removal only from the
paragraph beneath the route table. It required preserving altitude candidates,
candidate warnings, calculations and the API's `terrainLimitationNote` value.

The CLI returned `status: SUCCESS`, conversation
`9e5b57ee-9f44-4f28-91bf-e0889ab0751a`, with these decisions:

- Remove the quoted sentence only from the RouteWorkspace display.
- Add no replacement wording.
- Preserve all other warnings, calculation behavior and the API value.

The implementation removes the rendering paragraph and its unused style only.
The backend string and response contract remain unchanged. No new UI copy was
introduced. The confirmation was performed by Gemini, not inferred from a prior
approval or substituted with an implementation-agent review.
