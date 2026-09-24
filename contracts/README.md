# Vendored contracts

## satisfier-binding.schema.json

The OpenTeams manifest profile's draft `openteams/satisfier-binding [0.1-draft]`
schema, byte-identical to the copy vendored by the other suite providers
(cog-openrouter, cog-qwen, cog-turn-harness, cog-claude, cog-chatgpt).

SHA-256: `21315aa24a57ccdf02b8c5fba2b8517fd58876d63c6b04109483b06df9950130`

## src/system_one_contract.py

`openteams/system-one-turn [0.1-draft]`: the System One turn task and result.
Canonical source: cog-smith `templates/decision-cog/src/system_one_contract.py`
(decision-Cog machinery). Vendored byte-identically; fix it in Smith and copy.

SHA-256: `3ae7029d067107668c1196f9653ac6e3b9b7aef3e53abe9c2178ee0ef9606da3`

Updates must copy the agreed file deliberately, update the digest here, and
rerun this package's tests and Smith's decision-Cog tests.
