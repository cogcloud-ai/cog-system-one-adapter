---
type: cog [0.1]
name: cog-system-one-adapter
description: System One typed decisions answered by a separately admitted LLM through TypeSafe's MIT-licensed System One adapter.
version: "0.1.0"
license: Apache-2.0
publisher: OpenTeams
manifest: cog.yaml
manifest_schema: openteams/cog-manifest [0.1]
---

# System One Adapter Harness

An installable Harness provider for the `system-one/decisions` capability.
It answers the same typed questions as TypeSafe's Jev — Noul, Choice and
Score — by asking an ordinary LLM, using TypeSafe's own
[`system-one-adapter`](https://github.com/typesafe-ai/system-one-adapter-python)
(MIT, pinned 0.2.1) for prompting, output schema, decoding, probability
normalisation and confidence. It carries no model: a separately admitted
`model-endpoint/openai-compatible` binding supplies inference through its
loopback gateway (cog-qwen locally, or cog-openrouter).

Answers have System One's shape but not its calibration. Every result says
`answer_source: llm-adapter`; decision Cogs record it in `answered_by`.
Use it for local or offline work where state must not leave the machine, to
compare an LLM with Jev on the same questions, and for development without a
TypeSafe key. Tune thresholds per source.

Supports the three question types, text or JSON state, probability or
discrete answer modes, prompted JSON (default) or gateway JSON mode, and
bounded corrective re-asks. Strict JSON Schema output is not offered: a
gateway that enforces it turns a violation into an error that bypasses the
adapter's corrective re-asks. Every model call must carry the admitted gateway's provenance for
exactly the bound model; answers are checked against the questions asked.

Out of scope: owning or choosing a model, tools, memory, streaming, calling
model APIs directly, and claims of calibrated confidence. Provider candidates
never authorise themselves. See README.md.
