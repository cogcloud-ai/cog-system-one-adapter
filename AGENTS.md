# Cog System One Adapter contributor instructions
Read COG.md and README.md. This is a Harness provider for
`system-one/decisions`. It depends on TypeSafe's `system-one-adapter` and
`typesafe-sdk` (both MIT) pinned exactly in pixi.toml; the adapter reaches a
model ONLY through `GatewayProvider`, which calls a host-admitted loopback
gateway and requires that gateway's provenance. Never use the adapter's
built-in OpenAI/Anthropic/Gemini providers, which would bypass admission.
Vendored contracts (satisfier-binding schema, Smith's system_one_contract.py)
stay byte-identical; record digests in contracts/README.md.
Never present LLM-adapter answers as calibrated: `answer_source` stays
`llm-adapter`. Run pixi run test and pixi run check; tests are model-free and
exercise the real adapter against a fake gateway.
